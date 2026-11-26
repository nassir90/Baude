"""scenegfx — a scene drawn by pygfx (wgpu: Metal on macOS, Vulkan on Linux) instead of scenegl (a prototype): the
same SceneView (scenehost's layout, world matrices, panels as live widgets, ray picking for input), but its frame made
by a physically based renderer, offscreen, and handed to GTK as a texture.

What it draws that scenegl doesn't:
- PBR materials (metalness, roughness, a normal map and an occlusion/roughness/metalness map from textures/), lit by
  the scene's lights with shadows from its directional light (the sun), and by its environment: an HDR image
  (textures/*.hdr) whose light every surface reflects (image-based light).
- A backdrop: an equirectangular sky image drawn behind everything (in place of the nodes it stands for).
- Models (models/*.glb, glTF binary) drawn in a node's place, fitted to its unit box: its primitive mesh is still what
  a click is tested against, and what other renderers draw.
- A float frame: bloom from what's brighter than white, tone mapped to the screen in sRGB, antialiased (DDAA).

The panels are kept as they are: each is drawn unlit from its picture, marked in the frame's alpha, and the grading
pass leaves marked pixels alone (no bloom over them, no tone curve), so a panel's text is the widget's own.

Chosen by the scene (`"renderer": "gfx"`) or BAUDE_SCENE_RENDERER=gfx|gl (which wins); scene_view() makes the view."""
import json
import math
import os
import struct
import time

import numpy as np

import scenegl
import scenehost
from scenehost import Gdk, GLib, Graphene, Gtk

BLOOM = 0.07  # (scenegl's bloom strength → this bloom's: its blur is wider and energy-preserving)


def wanted(scene):
    """Whether a scene's to be drawn by pygfx: BAUDE_SCENE_RENDERER if set, else the scene's renderer."""
    env = os.environ.get("BAUDE_SCENE_RENDERER")
    return (env or scene.get("renderer") or "gl") == "gfx"


def scene_view(scene, world, providers, **kw):
    """A SceneView for the scene: drawn by pygfx where wanted (and pygfx is there), else by scenegl."""
    if wanted(scene):
        try:
            import pygfx  # noqa: F401
        except ImportError as e:
            print(f"scene: pygfx isn't installed ({e}); drawn in GL", flush=True)
        else:
            return GfxSceneView(scene, world, providers, **kw)
    return scenehost.SceneView(scene, world, providers, **kw)


# ---- reading a theme's files (data only: nothing in them is run)

def read_hdr(data):
    """A Radiance .hdr (RGBE, run-length encoded or flat) → float32 (h, w, 3), linear."""
    end = data.index(b"\n\n") + 2
    head = data[:end].decode("ascii", "replace")
    if "32-bit_rle_rgbe" not in head and "FORMAT" in head:
        raise ValueError("hdr: not RGBE")
    nl = data.index(b"\n", end)
    dims = data[end:nl].split()
    if dims[0] != b"-Y" or dims[2] != b"+X":
        raise ValueError("hdr: only -Y h +X w")
    h, w = int(dims[1]), int(dims[3])
    buf = np.frombuffer(data, np.uint8, offset=nl + 1)
    out = np.empty((h, w, 4), np.uint8)
    p = 0
    for y in range(h):
        if w < 8 or w > 0x7FFF or buf[p] != 2 or buf[p + 1] != 2 or buf[p + 2] & 0x80:
            out[y] = buf[p:p + w * 4].reshape(w, 4)  # (flat)
            p += w * 4
            continue
        p += 4
        for c in range(4):
            x = 0
            row = out[y, :, c]
            while x < w:
                n = int(buf[p])
                p += 1
                if n > 128:
                    n -= 128
                    row[x:x + n] = buf[p]
                    p += 1
                else:
                    row[x:x + n] = buf[p:p + n]
                    p += n
                x += n
    e = out[..., 3].astype(np.int32)
    scale = np.where(e > 0, np.ldexp(1.0, e - 136), 0.0).astype(np.float32)
    return out[..., :3].astype(np.float32) * scale[..., None]


def read_image(data, colorspace="srgb"):
    """A png/jpg → uint8 (h, w, 4), as GDK reads it."""
    t = Gdk.Texture.new_from_bytes(GLib.Bytes.new(data))
    d = Gdk.TextureDownloader.new(t)
    d.set_format(Gdk.MemoryFormat.R8G8B8A8)
    raw, stride = d.download_bytes()
    w, h = t.get_width(), t.get_height()
    return np.frombuffer(raw.get_data(), np.uint8).reshape(h, stride)[:, :w * 4].reshape(h, w, 4).copy()


GLB_TYPES = {5120: np.int8, 5121: np.uint8, 5122: np.int16, 5123: np.uint16, 5125: np.uint32, 5126: np.float32}
GLB_SIZES = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}


def _trs(node):
    if "matrix" in node:
        return np.array(node["matrix"], np.float64).reshape(4, 4).T
    t = node.get("translation", (0, 0, 0))
    x, y, z, w = node.get("rotation", (0, 0, 0, 1))
    s = node.get("scale", (1, 1, 1))
    r = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                  [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                  [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])
    m = np.eye(4)
    m[:3, :3] = r * np.array(s)[None, :]
    m[:3, 3] = t
    return m


def read_glb(data):
    """A glTF binary's triangles, by material: [(positions, normals, material dict)], in the model's space (its
    nodes' transforms applied). Materials as glTF has them (pbrMetallicRoughness, emissiveFactor, name)."""
    magic, version, _n = struct.unpack_from("<4sII", data, 0)
    if magic != b"glTF" or version != 2:
        raise ValueError("glb: not glTF 2 binary")
    jlen, jtype = struct.unpack_from("<II", data, 12)
    doc = json.loads(data[20:20 + jlen])
    blen, _btype = struct.unpack_from("<II", data, 20 + jlen)
    blob = memoryview(data)[28 + jlen:28 + jlen + blen]

    def accessor(i):
        a = doc["accessors"][i]
        v = doc["bufferViews"][a["bufferView"]]
        k = GLB_SIZES[a["type"]]
        dt = np.dtype(GLB_TYPES[a["componentType"]])
        off = v.get("byteOffset", 0) + a.get("byteOffset", 0)
        stride = v.get("byteStride", 0) or k * dt.itemsize
        raw = np.frombuffer(blob, np.uint8, count=stride * (a["count"] - 1) + k * dt.itemsize, offset=off)
        if stride == k * dt.itemsize:
            return raw.view(dt).reshape(a["count"], k).astype(np.float32 if dt == np.float32 else np.uint32)
        return np.stack([raw[j * stride:j * stride + k * dt.itemsize].view(dt) for j in range(a["count"])])

    out = []
    scene = doc.get("scenes", [{}])[doc.get("scene", 0)] if doc.get("scenes") else {"nodes": range(len(doc["nodes"]))}

    def walk(ni, parent):
        node = doc["nodes"][ni]
        m = parent @ _trs(node)
        if "mesh" in node:
            for prim in doc["meshes"][node["mesh"]]["primitives"]:
                if prim.get("mode", 4) != 4:
                    continue
                pos = accessor(prim["attributes"]["POSITION"]).astype(np.float64)
                nrm = accessor(prim["attributes"]["NORMAL"]).astype(np.float64) if "NORMAL" in prim["attributes"] else None
                idx = accessor(prim["indices"]).reshape(-1) if "indices" in prim else np.arange(len(pos))
                pos = (pos @ m[:3, :3].T + m[:3, 3])[idx]
                if nrm is not None:
                    nm = np.linalg.inv(m[:3, :3]).T
                    nrm = nrm @ nm.T
                    nrm = (nrm / np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-9))[idx]
                mat = doc.get("materials", [{}])[prim["material"]] if "material" in prim else {}
                out.append((pos.astype(np.float32), None if nrm is None else nrm.astype(np.float32), mat))
        for c in node.get("children", ()):
            walk(c, m)

    for ni in scene["nodes"]:
        walk(ni, np.eye(4))
    return out


def equirect_to_cube(img, size):
    """An equirectangular image (h, w, c), +y up, its middle column looking along -z → six cube faces (6, size,
    size, c) in pygfx's order (+x, -x, +y, -y, +z, -z), bilinear."""
    h, w = img.shape[:2]
    u = (np.arange(size) + 0.5) / size * 2 - 1
    uu, vv = np.meshgrid(u, -u)
    one = np.ones_like(uu)
    faces = []
    for x, y, z in ((one, vv, -uu), (-one, vv, uu), (uu, one, -vv), (uu, -one, vv), (uu, vv, one), (-uu, vv, -one)):
        d = np.stack([x, y, z], -1)
        d /= np.linalg.norm(d, axis=-1, keepdims=True)
        lon = np.arctan2(d[..., 0], -d[..., 2])  # (0 looking along -z, the image's middle)
        lat = np.arcsin(np.clip(d[..., 1], -1, 1))
        fx = (lon / (2 * np.pi) + 0.5) * w - 0.5
        fy = (0.5 - lat / np.pi) * h - 0.5
        x0, y0 = np.floor(fx).astype(int), np.floor(fy).astype(int)
        ax, ay = (fx - x0)[..., None], (fy - y0)[..., None]
        x0m, x1m = x0 % w, (x0 + 1) % w
        y0c, y1c = np.clip(y0, 0, h - 1), np.clip(y0 + 1, 0, h - 1)
        f = (img[y0c, x0m] * (1 - ax) * (1 - ay) + img[y0c, x1m] * ax * (1 - ay)
             + img[y1c, x0m] * (1 - ax) * ay + img[y1c, x1m] * ax * ay)
        faces.append(f)
    return np.stack(faces)


# ---- the frame's grading: bloom, the tone curve, panels left as they are

def _grade_pass():
    from pygfx.renderers.wgpu.engine.bloom import PhysicalBasedBloomPass
    from pygfx.renderers.wgpu.engine.effectpasses import FullQuadPass

    class Bright(FullQuadPass):
        """What blooms: what's brighter than the threshold, panels (alpha marked) left out."""
        uniform_type = dict(threshold="f4", knee="f4")
        wgsl = """
            @fragment
            fn fs_main(varyings: Varyings) -> @location(0) vec4<f32> {
                let c = textureSample(colorTex, texSampler, varyings.texCoord);
                let l = max(c.r, max(c.g, c.b));
                let k = clamp((l - u_effect.threshold) / max(u_effect.knee, 1e-4), 0.0, 1.0);
                let panel = 1.0 - c.a;
                return vec4<f32>(min(c.rgb * k * k, vec3<f32>(1.6)) * (1.0 - panel), 1.0);
            }
        """

    class Composite(FullQuadPass):
        """The frame: the scene plus its bloom, exposed and tone mapped (ACES, fitted), panels as they are."""
        uniform_type = dict(strength="f4", exposure="f4")
        wgsl = """
            fn aces(x: vec3<f32>) -> vec3<f32> {
                let m1 = mat3x3<f32>(0.59719, 0.07600, 0.02840, 0.35458, 0.90834, 0.13383, 0.04823, 0.01566, 0.83777);
                let m2 = mat3x3<f32>(1.60475, -0.10208, -0.00327, -0.53108, 1.10813, -0.07276, -0.07367, -0.00605, 1.07602);
                let v = m1 * x;
                let a = v * (v + 0.0245786) - 0.000090537;
                let b = v * (0.983729 * v + 0.4329510) + 0.238081;
                return clamp(m2 * (a / b), vec3<f32>(0.0), vec3<f32>(1.0));
            }
            @fragment
            fn fs_main(varyings: Varyings) -> @location(0) vec4<f32> {
                let c = textureSample(originalTex, texSampler, varyings.texCoord);
                let b = textureSample(bloomTex, texSampler, varyings.texCoord).rgb;
                let panel = clamp(1.0 - c.a, 0.0, 1.0);
                let lit = aces((c.rgb + b * u_effect.strength) * u_effect.exposure);
                return vec4<f32>(mix(lit, c.rgb, panel), 1.0);
            }
        """

    class Grade(PhysicalBasedBloomPass):
        def __init__(self, strength=0.6, threshold=1.0, knee=0.5, exposure=1.0, radius=0.0025):
            super().__init__(bloom_strength=strength, max_mip_levels=5, filter_radius=radius, use_karis_average=True)
            
            self.bright, self.compose = Bright(), Composite()
            self.threshold, self.knee, self.exposure = threshold, knee, exposure
            self._bright_tex = None

        def render(self, command_encoder, color_tex, depth_tex, target_tex):
            from pygfx.renderers.wgpu.engine.shared import get_shared
            src = color_tex.texture
            size = (src.size[0], src.size[1])
            if size != self._current_source_size or not self._bloom_texture:
                self._create_mip_bloom_texture(src)
                self._current_source_size = size
                self._bright_tex = get_shared().device.create_texture(
                    size=(size[0], size[1], 1), format=src.format,
                    usage=0x10 | 0x04)  # (RENDER_ATTACHMENT | TEXTURE_BINDING)
            self.bright._uniform_data["threshold"] = self.threshold
            self.bright._uniform_data["knee"] = self.knee
            bright = self._bright_tex.create_view()
            self.bright.render(command_encoder, colorTex=color_tex, targetTex=bright)
            self._perform_downsampling(command_encoder, bright)
            self._perform_upsampling(command_encoder)
            self.compose._uniform_data["strength"] = self.bloom_strength
            self.compose._uniform_data["exposure"] = self.exposure
            bloom = self._bloom_texture.create_view(base_mip_level=0, mip_level_count=1)
            self.compose.render(command_encoder, originalTex=color_tex, bloomTex=bloom, targetTex=target_tex)

    return Grade


# ---- the renderer

def _geometry(verts, repeat=(1, 1)):
    import pygfx as gfx
    a = np.array(verts, np.float32).reshape(-1, 8)
    uv = a[:, 6:8] * np.array(repeat, np.float32)
    return gfx.Geometry(positions=a[:, :3].copy(), normals=a[:, 3:6].copy(), texcoords=uv,
                        indices=np.arange(len(a), dtype=np.uint32).reshape(-1, 3))


def _mat4(m):
    return np.array(m.to_float(), np.float32).reshape(4, 4).T


def _srgb_to_linear(c):
    c = np.asarray(c, np.float32)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def _hex(c):
    c = c.lstrip("#")
    return [int(c[i:i + 2], 16) / 255 for i in (0, 2, 4)]


class Renderer:
    """A SceneView's scene in pygfx: its objects made as its nodes are shown, its panels' pictures uploaded when their
    widgets redraw, a frame rendered offscreen at (w, h) logical px × scale and read back as RGBA."""

    def __init__(self, view):
        import pygfx as gfx
        self.gfx, self.view = gfx, view
        self.scene = gfx.Scene()
        self.objects = {}  # (node id → (pygfx object, kind))
        self.geoms, self.models, self.textures, self.mats = {}, {}, {}, {}
        self.target = self.renderer = None
        self.size = None
        self.stats = {"render_ms": [], "readback_ms": [], "uploads": 0, "upload_ms": 0.0}
        sc = view.scene
        env = sc.get("environment") or {}
        self.env = None
        if env.get("texture"):
            hdr = read_hdr(view.asset(f"textures/{env['texture']}"))
            cube = equirect_to_cube(hdr, 256).astype(np.float32)
            rgba = np.concatenate([cube, np.ones(cube.shape[:-1] + (1,), np.float32)], -1)
            self.env = gfx.Texture(rgba, dim=2, size=(256, 256, 6), generate_mipmaps=True, colorspace="physical")
        self.env_k = float(env.get("intensity", 1.0))
        back = sc.get("backdrop") or {}
        self.hidden_materials = set(back.get("instead_of") or ())
        if back.get("texture"):
            img = read_image(view.asset(f"textures/{back['texture']}"))
            k = float(back.get("intensity", 1.0))
            lin = _srgb_to_linear(img[..., :3] / 255.0) * k
            cube = equirect_to_cube(lin, int(back.get("size", 1024))).astype(np.float32)
            rgba = np.concatenate([cube, np.ones(cube.shape[:-1] + (1,), np.float32)], -1).astype(np.float16)
            n = cube.shape[1]
            tex = gfx.Texture(rgba, dim=2, size=(n, n, 6), colorspace="physical")
            self.scene.add(gfx.Background(None, gfx.BackgroundSkyboxMaterial(map=tex)))
        else:
            c = _srgb_to_linear(_hex(sc.get("background", "#000000")))
            self.scene.add(gfx.Background(None, gfx.BackgroundMaterial(gfx.Color.from_physical(*c))))
        amb = sc.get("ambient", "#202020")
        self.scene.add(gfx.AmbientLight(amb, 1.0))
        self.sun = None
        for L in sc.get("lights") or ():
            k = float(L.get("intensity", 1.0))
            if L.get("type") == "directional":
                d = np.array(L.get("direction", (0, -1, 0)), float)
                d /= np.linalg.norm(d) or 1
                li = gfx.DirectionalLight(L.get("color", "#ffffff"), k * math.pi, cast_shadow=bool(L.get("shadow", True)))
                li.local.position = tuple(-d * 20)
                li.target.local.position = (0, 0, 0)
                li.shadow.camera.width = li.shadow.camera.height = 18
                li.shadow.camera.depth_range = (1, 60)
                li.shadow.bias = 0.0008
                li.shadow.normal_bias = 0.02
                self.sun = self.sun or li
            else:
                rng = float(L.get("range", 10))
                li = gfx.PointLight(L.get("color", "#ffffff"), k * 18, distance=rng * 1.6, decay=2)
                li.local.position = tuple(L.get("position", (0, 3, 0)))
            self.scene.add(li)
        self.specs = {s["id"]: s for s in sc["nodes"]}

    # -- assets
    def texture(self, name, colorspace="srgb", repeat=True):
        key = (name, colorspace)
        if key not in self.textures:
            img = read_image(self.view.asset(f"textures/{name}"))
            t = self.gfx.Texture(img, dim=2, generate_mipmaps=True, colorspace=colorspace)
            self.textures[key] = self.gfx.TextureMap(t, wrap="repeat" if repeat else "clamp")
        return self.textures[key]

    def geometry(self, mesh, repeat=(1, 1)):
        key = (mesh, tuple(repeat))
        if key not in self.geoms:
            make = {"plane": scenegl.plane, "box": scenegl.box, "cylinder": lambda: scenegl.cylinder(48),
                    "sphere": lambda: scenegl.sphere(48, 96)}.get(mesh)
            if make is None:
                verts = scenegl.obj(self.view.asset(f"models/{mesh}").decode("utf-8", "replace"))
            else:
                verts = make()
            self.geoms[key] = _geometry(verts, repeat)
        return self.geoms[key]

    def model(self, name):
        """A glb's parts as (geometry, its glTF material), fitted into the unit box (centred, each axis to 1)."""
        if name not in self.models:
            parts = read_glb(self.view.asset(f"models/{name}"))
            allp = np.concatenate([p for p, _n, _m in parts])
            lo, hi = allp.min(0), allp.max(0)
            c, s = (lo + hi) / 2, np.maximum(hi - lo, 1e-6)
            out = []
            for pos, nrm, mat in parts:
                p = (pos - c) / s
                if nrm is None:
                    tri = p.reshape(-1, 3, 3)
                    fn = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
                    nrm = np.repeat(fn / np.maximum(np.linalg.norm(fn, axis=1, keepdims=True), 1e-9), 3, 0)
                else:
                    nrm = nrm * s  # (the normals of the squashed shape)
                    nrm /= np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-9)
                g = self.gfx.Geometry(positions=p.astype(np.float32), normals=nrm.astype(np.float32),
                                      texcoords=np.zeros((len(p), 2), np.float32),
                                      indices=np.arange(len(p), dtype=np.uint32).reshape(-1, 3))
                out.append((g, mat))
            self.models[name] = out
        return self.models[name]

    # -- materials
    def material(self, m):
        """A scene material (SceneMaterial.json) as a pygfx standard material."""
        key = json.dumps(m, sort_keys=True)
        if key in self.mats:
            return self.mats[key]
        gfx = self.gfx
        col, em = m.get("color", "#ffffff"), m.get("emissive")
        glow = float(m.get("glow", 1.0))
        shin, spec = float(m.get("shininess", 32)), float(m.get("specular", 0.2))
        rough = m.get("roughness", min(1.0, max(0.12, math.sqrt(2 / (shin + 2)) * 1.6)))
        metal = m.get("metalness", min(0.85, spec * 0.7))
        kw = dict(color=col, roughness=float(rough), metalness=float(metal), env_map=self.env,
                  env_map_intensity=self.env_k)
        if em:
            kw.update(emissive=em, emissive_intensity=glow * 1.3)
        if m.get("texture"):  # (the texture's own colours: its colour is for renderers without it)
            kw["map"] = self.texture(m["texture"])
            kw["color"] = "#ffffff"
        if m.get("normal_texture"):
            kw["normal_map"] = self.texture(m["normal_texture"], "physical")
        if m.get("orm_texture"):
            orm = self.texture(m["orm_texture"], "physical")
            kw.update(roughness_map=orm, metalness_map=orm)
        mat = gfx.MeshStandardMaterial(**kw)
        self.mats[key] = mat
        return mat

    def glb_material(self, gm, over):
        """A model part's glTF material, with the node's material's roughness/metalness/tint over it."""
        key = json.dumps([gm.get("name"), gm.get("pbrMetallicRoughness"), gm.get("emissiveFactor"), over], sort_keys=True)
        if key in self.mats:
            return self.mats[key]
        pbr = gm.get("pbrMetallicRoughness") or {}
        r, g, b, _a = pbr.get("baseColorFactor", (1, 1, 1, 1))
        name = (gm.get("name") or "").lower()
        tint = _srgb_to_linear(_hex(over.get("color", "#ffffff"))) / _srgb_to_linear(_hex("#c0c4cc"))  # (its colour, as a shade of grey: 1 at #c0c4cc)
        metal = over.get("metalness", 0.75 if name.startswith("metal") else 0.15)
        rough = over.get("roughness", 0.38 if name.startswith("metal") else 0.6)
        kw = dict(color=self.gfx.Color.from_physical(r * tint[0], g * tint[1], b * tint[2]),
                  metalness=float(metal), roughness=float(rough), env_map=self.env, env_map_intensity=self.env_k)
        if name in ("_defaultmat", "glass"):  # (Kenney's glass: dark, glossy, lit a little from inside)
            kw.update(color="#1b2a3c", metalness=0.1, roughness=0.08, emissive=over.get("glass", "#2a4766"),
                      emissive_intensity=0.6)
        e = gm.get("emissiveFactor")
        if e and any(e):
            kw.update(emissive=self.gfx.Color.from_physical(*e), emissive_intensity=2.0)
        if over.get("texture"):
            kw["map"] = self.texture(over["texture"])
        if over.get("normal_texture"):
            kw["normal_map"] = self.texture(over["normal_texture"], "physical")
        mat = self.gfx.MeshStandardMaterial(**kw)
        self.mats[key] = mat
        return mat

    def panel_material(self, tex):
        """A panel: its picture as it is (unlit), marked in alpha 0 for the grading pass, depth written as an opaque."""
        m = self.gfx.MeshBasicMaterial(map=tex)
        m.alpha_config = {"method": "blended", "color_src": "one", "color_dst": "zero", "alpha_src": "zero",
                          "alpha_dst": "zero"}
        m.depth_write = True
        m.render_queue = 2000
        return m

    # -- the scene's objects, kept in step with its nodes
    def sync(self):
        v, gfx = self.view, self.gfx
        seen = set()
        for nid in v.order:
            n = v.nodes.get(nid)
            if n is None or not v.shown(n):
                continue
            mat = v.material(n)
            if n.mesh is not None:
                if n.material in self.hidden_materials:
                    continue
                seen.add(nid)
                ob = self.objects.get(nid)
                mkey = json.dumps(mat, sort_keys=True)
                if ob is None or ob[2] != mkey:
                    if ob is not None:
                        self.scene.remove(ob[0])
                    ob = (self.mesh_object(n, mat), "mesh", mkey)
                    self.objects[nid] = ob
                    self.scene.add(ob[0])
                ob[0].local.matrix = _mat4(v.world_matrix(n))
            elif n.panel is not None and n.face is not None and n.alloc is not None:
                seen.add(nid)
                tex = self.picture(n)
                if tex is None:
                    continue
                ob = self.objects.get(nid)
                if ob is None or ob[2] is not tex:
                    if ob is not None:
                        self.scene.remove(ob[0])
                    m = gfx.Mesh(self.geometry("plane"), self.panel_material(tex))
                    m.receive_shadow = m.cast_shadow = False
                    ob = (m, "panel", tex)
                    self.objects[nid] = ob
                    self.scene.add(m)
                sw, sh = n.panel.get("size") or (1.6, 1.2)
                ob[0].local.matrix = _mat4(Graphene.Matrix().init_scale(sw, sh, 1).multiply(v.world_matrix(n)))
        for nid in [k for k in self.objects if k not in seen]:
            self.scene.remove(self.objects.pop(nid)[0])

    def mesh_object(self, n, mat):
        gfx = self.gfx
        model = self.specs.get(n.id, {}).get("model")
        glowing = bool(mat.get("emissive")) and "specular" not in mat
        if model:
            grp = gfx.Group()
            for g, gm in self.model(model):
                part = gfx.Mesh(g, self.glb_material(gm, mat))
                part.cast_shadow = part.receive_shadow = True
                grp.add(part)
            return grp
        repeat = mat.get("repeat") or (1, 1)
        m = gfx.Mesh(self.geometry(n.mesh, repeat), self.material(mat))
        big = max(abs(x) for x in n.scale) > 8
        m.cast_shadow = not glowing and not big
        m.receive_shadow = not glowing and not big
        return m

    def picture(self, n):
        """n's panel's picture as a pygfx texture: the widget rendered by the window's GSK renderer and read back,
        again only after it redraws (as scenehost's)."""
        v = self.view
        pw, ph = v.panel_px(n)
        if not n.dirty and n.tex is not None and n.texsize == (pw, ph):
            return n.tex
        t0 = time.perf_counter()
        S = scenehost.PICTURE_SCALE
        sn = Gtk.Snapshot()
        sn.scale(S, S)
        sn.transform(n.alloc.invert())
        v.snapshot_child(n.face, sn)
        node = sn.to_node()
        native = v.get_native()
        if node is None or native is None:
            return n.tex
        tex = native.get_renderer().render_texture(node, Graphene.Rect().init(0, 0, pw * S, ph * S))
        d = Gdk.TextureDownloader.new(tex)
        d.set_format(Gdk.MemoryFormat.R8G8B8A8_PREMULTIPLIED)
        raw, stride = d.download_bytes()
        tw, th = tex.get_width(), tex.get_height()
        a = np.frombuffer(raw.get_data(), np.uint8).reshape(th, stride)[:, :tw * 4].reshape(th, tw, 4)
        if n.tex is not None and n.texsize == (pw, ph) and n.tex.texture.size[:2] == (tw, th):
            n.tex.texture.data[...] = a
            n.tex.texture.update_full()
        else:
            t = self.gfx.Texture(a.copy(), dim=2, generate_mipmaps=True)
            n.tex = self.gfx.TextureMap(t, filter="linear", wrap="clamp")
        n.texsize, n.dirty = (pw, ph), False
        self.stats["uploads"] += 1
        self.stats["upload_ms"] += (time.perf_counter() - t0) * 1000
        return n.tex

    # -- the camera, as scenehost's (the same eye, target and field of view)
    def camera(self, w, h):
        gfx = self.gfx
        cam = self.view.scene["camera"]
        fov = cam.get("fov", 45)
        if w < h:
            fov = math.degrees(2 * math.atan(math.tan(math.radians(fov / 2)) * h / max(w, 1)))
        a = w / max(h, 1)
        # (pygfx's fov spans the mean of width and height; scenehost's is vertical: the same view, said pygfx's way)
        mean = math.degrees(2 * math.atan(math.tan(math.radians(fov / 2)) * (1 + a) / 2))
        c = getattr(self, "_cam", None)
        if c is None:
            c = self._cam = gfx.PerspectiveCamera(mean, a, depth_range=(0.05, 200))
        c.fov, c.aspect = mean, a
        c.local.position = tuple(self.view.eye())
        c.look_at(tuple(cam["target"]))
        return c

    # -- a frame
    def ensure(self, pw, ph):
        gfx = self.gfx
        if self.size == (pw, ph):
            return
        self.size = (pw, ph)
        self.target = gfx.Texture(dim=2, size=(pw, ph, 1), format="rgba8unorm-srgb", usage=0x01)  # (COPY_SRC: read back)
        self.renderer = gfx.WgpuRenderer(self.target, pixel_ratio=1, pixel_scale=1, color_precision="float16",
                                         ppaa="none")
        sc = self.view.scene
        b = sc.get("bloom") or {}
        Grade = _grade_pass()
        from pygfx.renderers.wgpu.engine.effectpasses import DDAAPass
        self.grade = Grade(strength=float(b.get("strength", 0.6)) * BLOOM, threshold=float(b.get("threshold", 1.0)),
                           exposure=float(sc.get("exposure", 1.0)))
        self.renderer.effect_passes = [self.grade, DDAAPass()]

    def frame(self, w, h, scale=1):
        """The scene at (w, h) logical px × scale: uint8 (ph, pw, 4), sRGB. Times its render (to the GPU's finish)
        and its readback apart."""
        pw, ph = max(1, int(w * scale)), max(1, int(h * scale))
        self.ensure(pw, ph)
        self.sync()
        cam = self.camera(w, h)
        t0 = time.perf_counter()
        self.renderer.render(self.scene, cam)
        dev = self.renderer.device
        wt = self.target._wgpu_object
        dev.queue.read_texture({"texture": wt, "origin": (0, 0, 0), "mip_level": 0},
                               {"offset": 0, "bytes_per_row": 256}, (1, 1, 1))  # (a pixel: waits for the frame)
        t1 = time.perf_counter()
        bpr = (pw * 4 + 255) // 256 * 256
        raw = dev.queue.read_texture({"texture": wt, "origin": (0, 0, 0), "mip_level": 0},
                                     {"offset": 0, "bytes_per_row": bpr}, (pw, ph, 1))
        img = np.frombuffer(raw, np.uint8).reshape(ph, bpr)[:, :pw * 4].reshape(ph, pw, 4)
        t2 = time.perf_counter()
        self.stats["render_ms"].append((t1 - t0) * 1000)
        self.stats["readback_ms"].append((t2 - t1) * 1000)
        return img


# ---- the view: scenehost's SceneView, its GLArea swapped for a surface that shows pygfx's frame

class _Surface(Gtk.Widget):
    """Where scenehost's GLArea was: draws the view's pygfx frame as a texture."""

    def __init__(self, view):
        super().__init__(can_target=False)
        self.view = view

    def queue_render(self):
        self.queue_draw()

    def make_current(self):
        pass

    def get_error(self):
        return None

    def do_snapshot(self, s):
        self.view.draw(s)


class GfxSceneView(scenehost.SceneView):
    """A SceneView drawn by pygfx: see the module's notes. Input, layout and panels are scenehost's own."""

    def __init__(self, scene, world, providers, **kw):
        super().__init__(scene, world, providers, **kw)
        self.area.unparent()
        self.area = _Surface(self)
        self.area.set_parent(self)
        self.gfx = None
        self.stats["renderer"] = "gfx"

    def picture(self, n):
        return self.gfx.picture(n) if self.gfx is not None else None

    def draw(self, s):
        w, h = self.get_width(), self.get_height()
        if w <= 0 or h <= 0:
            return
        t0 = time.perf_counter()
        if self.gfx is None:
            self.gfx = Renderer(self)
        sc = self.get_scale_factor()
        img = self.gfx.frame(w, h, sc)
        ph, pw = img.shape[:2]
        tex = Gdk.MemoryTexture.new(pw, ph, Gdk.MemoryFormat.R8G8B8A8, GLib.Bytes.new(img.tobytes()), pw * 4)
        s.append_texture(tex, Graphene.Rect().init(0, 0, w, h))
        self.stats["render_ms"].append((time.perf_counter() - t0) * 1000)
