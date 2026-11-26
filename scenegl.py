"""scenegl — a scene's GL drawing, for scenehost's SceneView (a prototype): meshes and panels lit (Blinn-Phong: point
and directional lights, specular, emissive), with a depth buffer, drawn to a half-float frame, then bloom (what's
brighter than the threshold blurred and added). OpenGL 3.2 core through libepoxy (GTK's own GL loader) by ctypes, as
cards3d's GLCards: there's no PyOpenGL. Runs inside a Gtk.GLArea's render; nothing here is GTK but the context.

GSK can't do this itself on this GTK: GskGLShader (its custom shader node) is deprecated since 4.16 and the renderer
4.24 draws with refuses it ("The renderer does not support gl shaders"), and GSK has no depth buffer or lights."""
import array
import ctypes as C
import functools
import math
import os
import struct

SIGS = {
    "glCreateShader": ("u", "u"), "glShaderSource": (None, "u", "i", "pp", "p"), "glCompileShader": (None, "u"),
    "glCreateProgram": ("u",), "glAttachShader": (None, "u", "u"), "glLinkProgram": (None, "u"),
    "glUseProgram": (None, "u"), "glGetUniformLocation": ("i", "u", "c"), "glUniformMatrix4fv": (None, "i", "i", "u8", "fp"),
    "glUniform1f": (None, "i", "f"), "glUniform2f": (None, "i", "f", "f"), "glUniform3f": (None, "i", "f", "f", "f"),
    "glUniform4f": (None, "i", "f", "f", "f", "f"), "glUniform1i": (None, "i", "i"), "glUniform4fv": (None, "i", "i", "fp"),
    "glGenVertexArrays": (None, "i", "pu"), "glBindVertexArray": (None, "u"), "glGenBuffers": (None, "i", "pu"),
    "glBindBuffer": (None, "u", "u"), "glBufferData": (None, "u", "l", "p", "u"), "glDeleteBuffers": (None, "i", "pu"),
    "glDeleteVertexArrays": (None, "i", "pu"),
    "glEnableVertexAttribArray": (None, "u"), "glVertexAttribPointer": (None, "u", "i", "u", "u8", "i", "p"),
    "glGenTextures": (None, "i", "pu"), "glBindTexture": (None, "u", "u"), "glDeleteTextures": (None, "i", "pu"),
    "glTexImage2D": (None, "u", "i", "i", "i", "i", "i", "u", "u", "p"), "glTexParameteri": (None, "u", "u", "i"),
    "glActiveTexture": (None, "u"), "glDrawArrays": (None, "u", "i", "i"), "glClearColor": (None, "f", "f", "f", "f"),
    "glClear": (None, "u"), "glEnable": (None, "u"), "glDisable": (None, "u"), "glBlendFunc": (None, "u", "u"),
    "glViewport": (None, "i", "i", "i", "i"), "glDepthFunc": (None, "u"), "glDepthMask": (None, "u8"),
    "glGetShaderiv": (None, "u", "u", "pi"), "glGetShaderInfoLog": (None, "u", "i", "p", "p"),
    "glGetProgramiv": (None, "u", "u", "pi"), "glGetProgramInfoLog": (None, "u", "i", "p", "p"),
    "glBindAttribLocation": (None, "u", "u", "c"), "glGetIntegerv": (None, "u", "pi"), "glFinish": (None,),
    "glGenFramebuffers": (None, "i", "pu"), "glBindFramebuffer": (None, "u", "u"), "glDeleteFramebuffers": (None, "i", "pu"),
    "glFramebufferTexture2D": (None, "u", "u", "u", "u", "i"), "glGenRenderbuffers": (None, "i", "pu"),
    "glBindRenderbuffer": (None, "u", "u"), "glRenderbufferStorage": (None, "u", "u", "i", "i"),
    "glFramebufferRenderbuffer": (None, "u", "u", "u", "u"), "glDeleteRenderbuffers": (None, "i", "pu"),
    "glCheckFramebufferStatus": ("u", "u"), "glGenerateMipmap": (None, "u"),
    "glTexImage3D": (None, "u", "i", "i", "i", "i", "i", "i", "u", "u", "p"),
    "glTexSubImage3D": (None, "u", "i", "i", "i", "i", "i", "i", "i", "u", "u", "p"),
    "glFramebufferTextureLayer": (None, "u", "u", "u", "i", "i"),
    "glCopyTexSubImage3D": (None, "u", "i", "i", "i", "i", "i", "i", "i", "i"),
    "glBlitFramebuffer": (None, "i", "i", "i", "i", "i", "i", "i", "i", "u", "u"),
    "glDrawArraysInstanced": (None, "u", "i", "i", "i"), "glTexBuffer": (None, "u", "u", "u"),
    "glGetUniformBlockIndex": ("u", "u", "c"), "glUniformBlockBinding": (None, "u", "u", "u"),
    "glBindBufferBase": (None, "u", "u", "u"), "glFlush": (None,),
    "glGetTexLevelParameteriv": (None, "u", "i", "u", "pi"), "glIsTexture": ("u8", "u"),
    "glGetTexImage": (None, "u", "i", "u", "u", "p"),
}
TEXTURE_2D, RGBA, RGBA8, RGBA16F, UNSIGNED_BYTE, HALF_FLOAT, FLOAT = 0x0DE1, 0x1908, 0x8058, 0x881A, 0x1401, 0x140B, 0x1406
TRIANGLES, TRIANGLE_STRIP, ARRAY_BUFFER, STATIC_DRAW = 0x0004, 0x0005, 0x8892, 0x88E4
BLEND, DEPTH_TEST, ONE, ONE_MINUS_SRC_ALPHA, LEQUAL = 0x0BE2, 0x0B71, 1, 0x0303, 0x0203
COLOR_BUFFER_BIT, DEPTH_BUFFER_BIT = 0x4000, 0x0100
LINEAR, LINEAR_MIPMAP_LINEAR, MIN_FILTER, MAG_FILTER, WRAP_S, WRAP_T, CLAMP, REPEAT = \
    0x2601, 0x2703, 0x2801, 0x2800, 0x2802, 0x2803, 0x812F, 0x2901
VERTEX_SHADER, FRAGMENT_SHADER, COMPILE_STATUS, LINK_STATUS = 0x8B31, 0x8B30, 0x8B81, 0x8B82
FRAMEBUFFER, FRAMEBUFFER_BINDING, COLOR_ATTACHMENT0, DEPTH_ATTACHMENT, RENDERBUFFER, DEPTH24 = \
    0x8D40, 0x8CA6, 0x8CE0, 0x8D00, 0x8D41, 0x81A6
TEXTURE0 = 0x84C0
TEXTURE_2D_ARRAY, TEXTURE_BUFFER, UNIFORM_BUFFER, RGBA32F, STREAM_DRAW = 0x8C1A, 0x8C2A, 0x8A11, 0x8814, 0x88E0
READ_FRAMEBUFFER, DRAW_FRAMEBUFFER, MAX_LEVEL, NEAREST = 0x8CA8, 0x8CA9, 0x813D, 0x2600
TEXTURE_WIDTH, TEXTURE_HEIGHT = 0x1000, 0x1001
LIGHTS_MAX = 8
LEVELS = 6  # (a picture's mip levels: down to 1/32 of it, each copied into its layer on its own as it's made)


class GL:
    """The GL calls a scene needs, from libepoxy's dispatch pointers (the context current is the GLArea's)."""

    def __init__(self):
        path = next((p for p in ("/opt/homebrew/lib/libepoxy.0.dylib", "/usr/local/lib/libepoxy.0.dylib")
                     if os.path.exists(p)), None)
        lib = C.CDLL(path or "libepoxy.so.0")
        kinds = {"u": C.c_uint, "i": C.c_int, "f": C.c_float, "l": C.c_ssize_t, "p": C.c_void_p, "c": C.c_char_p,
                 "u8": C.c_ubyte, "pp": C.POINTER(C.c_char_p), "pu": C.POINTER(C.c_uint), "pi": C.POINTER(C.c_int),
                 "fp": C.POINTER(C.c_float), None: None}
        for name, (ret, *args) in SIGS.items():
            fn = C.CFUNCTYPE(kinds[ret], *[kinds[a] for a in args])(C.c_void_p.in_dll(lib, "epoxy_" + name).value)
            setattr(self, name[2:], fn)

    def program(self, vert, frag, attribs=("pos", "nrm", "uv")):
        prog = self.CreateProgram()
        for kind, src in ((VERTEX_SHADER, vert), (FRAGMENT_SHADER, frag)):
            sh = self.CreateShader(kind)
            self.ShaderSource(sh, 1, (C.c_char_p * 1)(src.encode()), None)
            self.CompileShader(sh)
            ok = C.c_int(0)
            self.GetShaderiv(sh, COMPILE_STATUS, C.byref(ok))
            if not ok.value:
                log = C.create_string_buffer(4000)
                self.GetShaderInfoLog(sh, 4000, None, log)
                raise RuntimeError("shader: " + log.value.decode(errors="replace"))
            self.AttachShader(prog, sh)
        for i, a in enumerate(attribs):
            self.BindAttribLocation(prog, i, a.encode())
        self.LinkProgram(prog)
        ok = C.c_int(0)
        self.GetProgramiv(prog, LINK_STATUS, C.byref(ok))
        if not ok.value:
            log = C.create_string_buffer(4000)
            self.GetProgramInfoLog(prog, 4000, None, log)
            raise RuntimeError("program: " + log.value.decode(errors="replace"))
        return prog

    def texture(self, data, w, h, tid=None, internal=RGBA8, kind=UNSIGNED_BYTE, mipmap=False, wrap=CLAMP):
        if tid is None:
            t = C.c_uint()
            self.GenTextures(1, C.byref(t))
            tid = t.value
        self.BindTexture(TEXTURE_2D, tid)
        self.TexParameteri(TEXTURE_2D, MIN_FILTER, LINEAR_MIPMAP_LINEAR if mipmap else LINEAR)
        self.TexParameteri(TEXTURE_2D, MAG_FILTER, LINEAR)
        self.TexParameteri(TEXTURE_2D, WRAP_S, wrap)
        self.TexParameteri(TEXTURE_2D, WRAP_T, wrap)
        buf = None if data is None else C.create_string_buffer(bytes(data), len(data))
        self.TexImage2D(TEXTURE_2D, 0, internal, w, h, 0, RGBA, kind, buf)
        if mipmap:
            self.GenerateMipmap(TEXTURE_2D)
        return tid

    def delete_texture(self, tid):
        self.DeleteTextures(1, C.byref(C.c_uint(tid)))


# ---- meshes: interleaved pos(3) normal(3) uv(2), triangles

def plane():
    """1 by 1 in x and y, facing +z; v=0 at its top (a panel's picture's first row)."""
    v = []
    for (u, w) in ((0, 0), (0, 1), (1, 1), (0, 0), (1, 1), (1, 0)):
        v += [u - 0.5, 0.5 - w, 0, 0, 0, 1, u, w]
    return v


def box():
    out = []
    faces = (((1, 0, 0), (0, 0, -1), (0, 1, 0)), ((-1, 0, 0), (0, 0, 1), (0, 1, 0)), ((0, 1, 0), (1, 0, 0), (0, 0, -1)),
             ((0, -1, 0), (1, 0, 0), (0, 0, 1)), ((0, 0, 1), (1, 0, 0), (0, 1, 0)), ((0, 0, -1), (-1, 0, 0), (0, 1, 0)))
    for n, a, b in faces:
        def p(s, t):
            return [n[i] * 0.5 + a[i] * s + b[i] * t for i in range(3)]
        for s, t in ((-0.5, -0.5), (0.5, -0.5), (0.5, 0.5), (-0.5, -0.5), (0.5, 0.5), (-0.5, 0.5)):
            out += p(s, t) + list(n) + [s + 0.5, 0.5 - t]
    return out


def cylinder(sides=32):
    out = []
    for i in range(sides):
        a0, a1 = 2 * math.pi * i / sides, 2 * math.pi * (i + 1) / sides
        c0, s0, c1, s1 = math.cos(a0), math.sin(a0), math.cos(a1), math.sin(a1)
        u0, u1 = i / sides, (i + 1) / sides
        for (c, s, y, u) in ((c0, s0, -0.5, u0), (c1, s1, -0.5, u1), (c1, s1, 0.5, u1),
                             (c0, s0, -0.5, u0), (c1, s1, 0.5, u1), (c0, s0, 0.5, u0)):
            out += [c * 0.5, y, s * 0.5, c, 0, s, u, 0.5 - y]
        for y, ny in ((0.5, 1), (-0.5, -1)):
            for (c, s) in ((0, 0), (c0, s0), (c1, s1)) if ny > 0 else ((0, 0), (c1, s1), (c0, s0)):
                out += [c * 0.5, y, s * 0.5, 0, ny, 0, c * 0.5 + 0.5, s * 0.5 + 0.5]
    return out


def sphere(rings=16, sides=32):
    def p(r, s):
        th, ph = math.pi * r / rings, 2 * math.pi * s / sides
        n = (math.sin(th) * math.cos(ph), math.cos(th), math.sin(th) * math.sin(ph))
        return [x * 0.5 for x in n] + list(n) + [s / sides, r / rings]
    out = []
    for r in range(rings):
        for s in range(sides):
            a, b, c, d = p(r, s), p(r + 1, s), p(r + 1, s + 1), p(r, s + 1)
            out += a + b + c + a + c + d
    return out


def obj(text):
    """A Wavefront .obj's triangles (v, vn, vt, f; faces fanned), as the other meshes are; normals made flat where
    it has none."""
    vs, ns, ts, out = [], [], [], []
    for line in text.splitlines():
        p = line.split()
        if not p:
            continue
        if p[0] == "v":
            vs.append([float(x) for x in p[1:4]])
        elif p[0] == "vn":
            ns.append([float(x) for x in p[1:4]])
        elif p[0] == "vt":
            ts.append([float(x) for x in p[1:3]] + [0.0] * (2 - len(p[1:3])))
        elif p[0] == "f":
            idx = []
            for c in p[1:]:
                f = (c.split("/") + ["", ""])[:3]
                idx.append(tuple(int(x) - 1 if x and int(x) > 0 else (int(x) if x else None) for x in f))
            for k in range(1, len(idx) - 1):
                tri = [idx[0], idx[k], idx[k + 1]]
                pts = [vs[t[0]] for t in tri]
                e1 = [pts[1][i] - pts[0][i] for i in range(3)]
                e2 = [pts[2][i] - pts[0][i] for i in range(3)]
                fn = [e1[1] * e2[2] - e1[2] * e2[1], e1[2] * e2[0] - e1[0] * e2[2], e1[0] * e2[1] - e1[1] * e2[0]]
                ln = math.sqrt(sum(x * x for x in fn)) or 1
                fn = [x / ln for x in fn]
                for t, pt in zip(tri, pts):
                    n = ns[t[2]] if t[2] is not None and t[2] < len(ns) else fn
                    uv = ts[t[1]] if t[1] is not None and t[1] < len(ts) else [0, 0]
                    out += pt + list(n) + [uv[0], 1 - uv[1]]
    return out


def bounds(verts):
    xs, ys, zs = verts[0::8], verts[1::8], verts[2::8]
    return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))


# ---- shaders

FRAME_BLOCK = """layout(std140) uniform Frame {
    mat4 viewproj; vec4 eye; vec4 ambient; ivec4 counts; vec4 lpos[8]; vec4 lcol[8];
};
"""
LIT_VERT = """#version 150
in vec3 pos; in vec3 nrm; in vec2 uv;
""" + FRAME_BLOCK + """uniform samplerBuffer inst; uniform int base;
out vec3 wpos; out vec3 wnrm; out vec2 tuv;
flat out vec4 kd; flat out vec4 ke; flat out vec4 kx;
void main() {
    int i = (base + gl_InstanceID) * 7;  // (an instance: its model's 4 columns, then its material's 3 texels)
    mat4 model = mat4(texelFetch(inst, i), texelFetch(inst, i + 1), texelFetch(inst, i + 2), texelFetch(inst, i + 3));
    kd = texelFetch(inst, i + 4); ke = texelFetch(inst, i + 5); kx = texelFetch(inst, i + 6);
    vec4 w = model * vec4(pos, 1.0);
    wpos = w.xyz; wnrm = mat3(transpose(inverse(model))) * nrm; tuv = uv;
    gl_Position = viewproj * w;
}
"""
LIT_FRAG = """#version 150
in vec3 wpos; in vec3 wnrm; in vec2 tuv;
flat in vec4 kd; flat in vec4 ke; flat in vec4 kx;  // (color, specular; emissive, glow; shininess, picture's layer)
""" + FRAME_BLOCK + """uniform sampler2D tex; uniform sampler2DArray pics; uniform int mode;  // (0 plain, 1 tex, 2 a picture)
out vec4 frag;
void main() {
    vec4 base = mode == 2 ? texture(pics, vec3(tuv, kx.y)) : mode == 1 ? texture(tex, tuv) : vec4(1.0);
    // (premultiplied: a panel's picture)
    if (base.a < 0.004) discard;
    vec3 albedo = base.rgb * kd.rgb;
    vec3 n = normalize(wnrm);
    vec3 v = normalize(eye.xyz - wpos);
    if (dot(n, v) < 0.0) n = -n;  // (a panel or a plane seen from behind is lit as its front)
    vec3 lit = ambient.rgb * albedo;
    for (int i = 0; i < counts.x; i++) {
        vec3 l; float fall = 1.0;
        if (lpos[i].w > 0.0) {
            vec3 d = lpos[i].xyz - wpos; float dist = length(d); l = d / dist;
            fall = clamp(1.0 - dist / lpos[i].w, 0.0, 1.0); fall *= fall;
        } else {
            l = normalize(-lpos[i].xyz);
        }
        float diff = max(dot(n, l), 0.0);
        vec3 h = normalize(l + v);
        float spec = diff > 0.0 ? pow(max(dot(n, h), 0.0), kx.x) * kd.a : 0.0;
        lit += (albedo * diff + vec3(spec) * base.a) * lcol[i].rgb * fall;
    }
    lit += ke.rgb * (mode != 0 ? base.rgb : vec3(1.0)) * ke.a;
    frag = vec4(lit, base.a);
}
"""
QUAD_VERT = """#version 150
in vec3 pos;
out vec2 tuv;
void main() { tuv = pos.xy * 0.5 + 0.5; gl_Position = vec4(pos.xy, 0.0, 1.0); }
"""
BRIGHT_FRAG = """#version 150
in vec2 tuv; uniform sampler2D src; uniform float threshold; out vec4 frag;
void main() { vec3 c = texture(src, tuv).rgb; frag = vec4(max(c - vec3(threshold), 0.0), 1.0); }
"""
BLUR_FRAG = """#version 150
in vec2 tuv; uniform sampler2D src; uniform vec2 step; out vec4 frag;
void main() {
    float w[5] = float[](0.227027, 0.1945946, 0.1216216, 0.054054, 0.016216);
    vec3 c = texture(src, tuv).rgb * w[0];
    for (int i = 1; i < 5; i++) {
        c += texture(src, tuv + step * float(i)).rgb * w[i];
        c += texture(src, tuv - step * float(i)).rgb * w[i];
    }
    frag = vec4(c, 1.0);
}
"""
COMPOSE_FRAG = """#version 150
in vec2 tuv; uniform sampler2D scene; uniform sampler2D bloom; uniform float strength; out vec4 frag;
void main() {
    vec3 c = texture(scene, tuv).rgb + texture(bloom, tuv).rgb * strength;
    frag = vec4(min(c, vec3(1.0)), 1.0);
}
"""

@functools.lru_cache(maxsize=1024)
def rgb(hexcolour, scale=1.0):
    h = (hexcolour or "#000000").lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 * scale for i in (0, 2, 4))


class Target:
    """A frame drawn into: a colour texture (half float) and, if asked, a depth buffer."""

    def __init__(self, gl, w, h, depth):
        self.w, self.h = w, h
        f = C.c_uint()
        gl.GenFramebuffers(1, C.byref(f))
        self.fbo = f.value
        gl.BindFramebuffer(FRAMEBUFFER, self.fbo)
        self.tex = gl.texture(None, w, h, internal=RGBA16F, kind=HALF_FLOAT)
        gl.FramebufferTexture2D(FRAMEBUFFER, COLOR_ATTACHMENT0, TEXTURE_2D, self.tex, 0)
        self.rb = None
        if depth:
            r = C.c_uint()
            gl.GenRenderbuffers(1, C.byref(r))
            self.rb = r.value
            gl.BindRenderbuffer(RENDERBUFFER, self.rb)
            gl.RenderbufferStorage(RENDERBUFFER, DEPTH24, w, h)
            gl.FramebufferRenderbuffer(FRAMEBUFFER, DEPTH_ATTACHMENT, RENDERBUFFER, self.rb)
        status = gl.CheckFramebufferStatus(FRAMEBUFFER)
        if status != 0x8CD5:
            raise RuntimeError(f"framebuffer incomplete: {status:#x}")

    def free(self, gl):
        gl.DeleteFramebuffers(1, C.byref(C.c_uint(self.fbo)))
        gl.delete_texture(self.tex)
        if self.rb:
            gl.DeleteRenderbuffers(1, C.byref(C.c_uint(self.rb)))


class Pictures:
    """Panels' pictures of one size: the layers of one array texture, each mipmapped on its own (so a picture made
    again costs its own levels, not every panel's), the array grown as more are wanted and the layers kept."""

    def __init__(self, r, w, h):
        self.r, self.w, self.h = r, w, h
        self.levels = min(LEVELS, 1 + int(math.log2(max(w, h, 1))))
        self.tex, self.cap, self.used, self.free = None, 0, 0, []

    def _alloc(self, cap):
        gl = self.r.gl
        gl.ActiveTexture(TEXTURE0 + 1)
        t = C.c_uint()
        gl.GenTextures(1, C.byref(t))
        gl.BindTexture(TEXTURE_2D_ARRAY, t.value)
        for lv in range(self.levels):
            gl.TexImage3D(TEXTURE_2D_ARRAY, lv, RGBA8, max(1, self.w >> lv), max(1, self.h >> lv), cap, 0, RGBA,
                          UNSIGNED_BYTE, None)
        gl.TexParameteri(TEXTURE_2D_ARRAY, MIN_FILTER, LINEAR_MIPMAP_LINEAR)
        gl.TexParameteri(TEXTURE_2D_ARRAY, MAG_FILTER, LINEAR)
        gl.TexParameteri(TEXTURE_2D_ARRAY, WRAP_S, CLAMP)
        gl.TexParameteri(TEXTURE_2D_ARRAY, WRAP_T, CLAMP)
        gl.TexParameteri(TEXTURE_2D_ARRAY, MAX_LEVEL, self.levels - 1)
        return t.value

    def take(self, hint=0):
        """A layer for a picture (the array grown, its pictures copied over, if it's full)."""
        if self.free:
            return self.free.pop()
        if self.used == self.cap:
            cap = max(self.used + 1, hint, self.cap + self.cap // 2, 8)
            tex, gl = self._alloc(cap), self.r.gl
            if self.tex is not None:
                gl.BindFramebuffer(READ_FRAMEBUFFER, self.r.copy_fbo)
                for layer in range(self.used):
                    gl.FramebufferTextureLayer(READ_FRAMEBUFFER, COLOR_ATTACHMENT0, self.tex, 0, layer)
                    gl.CopyTexSubImage3D(TEXTURE_2D_ARRAY, 0, 0, 0, layer, 0, 0, self.w, self.h)
                gl.GenerateMipmap(TEXTURE_2D_ARRAY)  # (once, as it grows)
                gl.delete_texture(self.tex)
            gl.ActiveTexture(TEXTURE0)
            self.tex, self.cap = tex, cap
        self.used += 1
        return self.used - 1

    def give(self, layer):
        self.free.append(layer)

    def put(self, layer, data=None, source=None, flipped=False):
        """A layer's picture: data (rows of RGBA8, premultiplied, top first) or a GL texture of the size (another
        context's, shared, upside down if flipped). It's put in a texture of its own first (made again each time, so
        the GPU's never waited for), mipmapped there, and each level copied into the layer on the GPU."""
        gl, r = self.r.gl, self.r
        out = C.c_int(0)
        gl.GetIntegerv(FRAMEBUFFER_BINDING, C.byref(out))
        gl.ActiveTexture(TEXTURE0)
        st = r.staging(self.w, self.h, data)
        if data is None:
            gl.BindFramebuffer(READ_FRAMEBUFFER, r.copy_fbo)
            gl.FramebufferTexture2D(READ_FRAMEBUFFER, COLOR_ATTACHMENT0, TEXTURE_2D, source, 0)
            gl.BindFramebuffer(DRAW_FRAMEBUFFER, r.put_fbo)
            gl.FramebufferTexture2D(DRAW_FRAMEBUFFER, COLOR_ATTACHMENT0, TEXTURE_2D, st, 0)
            gl.BlitFramebuffer(0, 0, self.w, self.h, 0, self.h if flipped else 0, self.w, 0 if flipped else self.h,
                               COLOR_BUFFER_BIT, NEAREST)
        gl.GenerateMipmap(TEXTURE_2D)
        gl.BindFramebuffer(READ_FRAMEBUFFER, r.copy_fbo)
        gl.ActiveTexture(TEXTURE0 + 1)  # (the unit the shader reads pictures from: the array's copied into there)
        gl.BindTexture(TEXTURE_2D_ARRAY, self.tex)
        for lv in range(self.levels):
            gl.FramebufferTexture2D(READ_FRAMEBUFFER, COLOR_ATTACHMENT0, TEXTURE_2D, st, lv)
            gl.CopyTexSubImage3D(TEXTURE_2D_ARRAY, lv, 0, 0, layer, 0, 0, max(1, self.w >> lv), max(1, self.h >> lv))
        gl.ActiveTexture(TEXTURE0)
        gl.FramebufferTexture2D(READ_FRAMEBUFFER, COLOR_ATTACHMENT0, TEXTURE_2D, 0, 0)
        gl.BindFramebuffer(FRAMEBUFFER, out.value)

    def drop(self):
        if self.tex is not None:
            self.r.gl.delete_texture(self.tex)
            self.tex = None


HEAD = struct.Struct("9f")  # (an instance's material: color, specular; emissive, glow; shininess; then its layer, -, -)
FRAME = struct.Struct("16f4f4f4i32f32f")  # (the Frame block, std140)


def material(mat):
    """A material's part of an instance (after its model's 16 floats, before layer()'s), as the shaders take it."""
    return HEAD.pack(*rgb(mat.get("color", "#ffffff")), float(mat.get("specular", 0.2)),
                     *rgb(mat.get("emissive", "#000000")), float(mat.get("glow", 1.0)), float(mat.get("shininess", 32)))


@functools.lru_cache(maxsize=4096)
def layer(n=0):
    """An instance's last part: its picture's layer."""
    return struct.pack("3f", float(n), 0.0, 0.0)


def model(m):
    """A Graphene.Matrix as an instance's model (its 4 columns as GLSL's mat4 takes them, for m * v)."""
    return array.array("f", m.to_float()).tobytes()


class Renderer:
    """Draws a frame: what SceneView hands it (meshes and panels, each an instance: its model matrix and material,
    packed; the camera; the lights), into the GLArea's own framebuffer, with bloom. Each frame's instances go to the GPU
    in one buffer, and the frame's lights and camera in one uniform buffer: a draw for each mesh and texture, and one
    for each run of panels (far first) whose pictures share an array."""

    def __init__(self):
        self.gl = gl = GL()
        self.lit = gl.program(LIT_VERT, LIT_FRAG)
        self.bright = gl.program(QUAD_VERT, BRIGHT_FRAG, ("pos",))
        self.blur = gl.program(QUAD_VERT, BLUR_FRAG, ("pos",))
        self.compose = gl.program(QUAD_VERT, COMPOSE_FRAG, ("pos",))
        u = lambda prog, name: gl.GetUniformLocation(prog, name.encode())  # noqa: E731
        self.loc = {n: u(self.lit, n) for n in ("inst", "base", "tex", "pics", "mode")}
        self.bright_loc = (u(self.bright, "threshold"), u(self.bright, "src"))
        self.blur_loc = (u(self.blur, "step"), u(self.blur, "src"))
        self.compose_loc = (u(self.compose, "scene"), u(self.compose, "bloom"), u(self.compose, "strength"))
        gl.UseProgram(self.lit)
        gl.Uniform1i(self.loc["tex"], 0)
        gl.Uniform1i(self.loc["pics"], 1)
        gl.Uniform1i(self.loc["inst"], 2)
        gl.UniformBlockBinding(self.lit, gl.GetUniformBlockIndex(self.lit, b"Frame"), 0)
        self.ubo, self.ibo = self._buffer(), self._buffer()
        gl.BindBuffer(UNIFORM_BUFFER, self.ubo)
        gl.BufferData(UNIFORM_BUFFER, FRAME.size, None, STREAM_DRAW)
        gl.BindBufferBase(UNIFORM_BUFFER, 0, self.ubo)
        gl.BindBuffer(TEXTURE_BUFFER, self.ibo)
        gl.BufferData(TEXTURE_BUFFER, 112, None, STREAM_DRAW)
        t = C.c_uint()
        gl.GenTextures(1, C.byref(t))
        self.itex = t.value
        gl.BindTexture(TEXTURE_BUFFER, self.itex)
        gl.TexBuffer(TEXTURE_BUFFER, RGBA32F, self.ibo)
        self.copy_fbo, self.put_fbo = self._fbo(), self._fbo()
        self.staged = {}  # ((w, h) → a texture a picture's put in first)
        self.meshes = {}  # (name → (vao, count, bounds))
        self.quad = self.mesh("_quad", [x for p in ((-1, -1), (1, -1), (-1, 1), (1, 1)) for x in (p[0], p[1], 0, 0, 0, 1, 0, 0)])
        self.targets, self.size = None, None
        self.textures = {}  # (asset path → GL id)
        self.pictures = {}  # ((w, h) → Pictures)

    def _buffer(self):
        b = C.c_uint()
        self.gl.GenBuffers(1, C.byref(b))
        return b.value

    def _fbo(self):
        f = C.c_uint()
        self.gl.GenFramebuffers(1, C.byref(f))
        return f.value

    def mesh(self, name, verts):
        if name in self.meshes:
            return self.meshes[name]
        gl = self.gl
        vao, vbo = C.c_uint(), C.c_uint()
        gl.GenVertexArrays(1, C.byref(vao))
        gl.BindVertexArray(vao.value)
        gl.GenBuffers(1, C.byref(vbo))
        gl.BindBuffer(ARRAY_BUFFER, vbo.value)
        arr = (C.c_float * len(verts))(*verts)
        gl.BufferData(ARRAY_BUFFER, C.sizeof(arr), arr, STATIC_DRAW)
        for i, (n, off) in enumerate(((3, 0), (3, 12), (2, 24))):
            gl.EnableVertexAttribArray(i)
            gl.VertexAttribPointer(i, n, FLOAT, 0, 32, C.c_void_p(off))
        self.meshes[name] = (vao.value, len(verts) // 8, bounds(verts))
        return self.meshes[name]

    def staging(self, w, h, data):
        """The texture a w by h picture's put in first, bound: data in it (its storage made again, the last picture's
        let go of as the GPU's done with it), or storage alone."""
        tid = self.staged.get((w, h))
        if tid is None:
            tid = self.staged[(w, h)] = self.gl.texture(None, w, h, mipmap=True)
        self.gl.BindTexture(TEXTURE_2D, tid)
        if data is not None:
            self.gl.TexImage2D(TEXTURE_2D, 0, RGBA8, w, h, 0, RGBA, UNSIGNED_BYTE, data)
        return tid

    def picture(self, slot, w, h, data=None, source=None, flipped=False, hint=0):
        """A panel's picture put in (data: w by h RGBA8 rows, premultiplied, top first; or source, a GL texture id
        of another context's, shared, drawn upside down if flipped): in its own layer of the array for its size.
        slot: where its last one is ((Pictures, layer), or None); returns where this one is."""
        if slot is not None and (slot[0].w, slot[0].h) != (w, h):
            self.free_picture(slot)
            slot = None
        if slot is None:
            group = self.pictures.get((w, h))
            if group is None:
                group = self.pictures[(w, h)] = Pictures(self, w, h)
            slot = (group, group.take(hint))
        slot[0].put(slot[1], data, source, flipped)
        return slot

    def read(self, tid, w, h):
        """A w by h GL texture's level 0 read back (RGBA8 rows, GL's first row first), or None if it isn't one here."""
        gl = self.gl
        if not gl.IsTexture(tid):
            return None
        gl.ActiveTexture(TEXTURE0)
        gl.BindTexture(TEXTURE_2D, tid)
        iw, ih = C.c_int(), C.c_int()
        gl.GetTexLevelParameteriv(TEXTURE_2D, 0, TEXTURE_WIDTH, C.byref(iw))
        gl.GetTexLevelParameteriv(TEXTURE_2D, 0, TEXTURE_HEIGHT, C.byref(ih))
        if (iw.value, ih.value) != (w, h):
            return None
        buf = C.create_string_buffer(w * h * 4)
        gl.GetTexImage(TEXTURE_2D, 0, RGBA, UNSIGNED_BYTE, buf)
        return buf.raw

    def free_picture(self, slot):
        if slot is not None:
            slot[0].give(slot[1])

    def _targets(self, w, h):
        if self.size == (w, h):
            return
        if self.targets:
            for t in self.targets:
                t.free(self.gl)
        hw, hh = max(1, w // 2), max(1, h // 2)
        self.targets = [Target(self.gl, w, h, True), Target(self.gl, hw, hh, False), Target(self.gl, hw, hh, False)]
        self.size = (w, h)

    def frame(self, w, h, view, proj, eye, scene, meshes, panels):
        """One frame, w by h device pixels. meshes: (mesh name, GL texture id or None, instance) each, opaque; panels:
        (distance from the eye, Pictures, instance) each, drawn far first; an instance: model() then material()'s
        bytes. scene: background, ambient, lights, bloom."""
        gl = self.gl
        out = C.c_int(0)
        gl.GetIntegerv(FRAMEBUFFER_BINDING, C.byref(out))  # (the GLArea's: drawn into last)
        self._targets(w, h)
        main, half_a, half_b = self.targets
        runs, chunks, at = [], [], 0  # (each draw: vao, count, mode, texture, first instance, instances)
        groups = {}
        for mesh, tex, inst in meshes:
            groups.setdefault((mesh, tex or 0), []).append(inst)
        for (mesh, tex), insts in groups.items():
            vao, count, _b = self.meshes[mesh]
            runs.append((vao, count, 1 if tex else 0, tex, at, len(insts)))
            chunks += insts
            at += len(insts)
        panels.sort(key=lambda p: -p[0])
        pvao, pcount, _b = self.meshes["plane"]
        for _d, pics, inst in panels:
            if runs and runs[-1][3] == pics.tex and runs[-1][2] == 2 and runs[-1][4] + runs[-1][5] == at:
                r = runs[-1]
                runs[-1] = (*r[:5], r[5] + 1)
            else:
                runs.append((pvao, pcount, 2, pics.tex, at, 1))
            chunks.append(inst)
            at += 1
        lights = scene.get("lights") or []
        pos, col = [0.0] * (4 * LIGHTS_MAX), [0.0] * (4 * LIGHTS_MAX)
        nl = 0
        for light in lights[:LIGHTS_MAX]:
            k = light.get("intensity", 1.0)
            if light.get("type") == "directional":
                pos[nl * 4:nl * 4 + 4] = list(light.get("direction") or (0, -1, 0)) + [0.0]
            else:
                pos[nl * 4:nl * 4 + 4] = list(light.get("position") or (0, 3, 0)) + [float(light.get("range", 10.0))]
            col[nl * 4:nl * 4 + 4] = list(rgb(light.get("color", "#ffffff"), k)) + [1.0]
            nl += 1
        block = FRAME.pack(*view.multiply(proj).to_float(), *eye, 1.0, *rgb(scene.get("ambient", "#202020")), 1.0,
                           nl, 0, 0, 0, *pos, *col)
        gl.BindBuffer(UNIFORM_BUFFER, self.ubo)
        gl.BufferData(UNIFORM_BUFFER, FRAME.size, block, STREAM_DRAW)
        data = b"".join(chunks)
        gl.BindBuffer(TEXTURE_BUFFER, self.ibo)
        gl.BufferData(TEXTURE_BUFFER, max(len(data), 112), data or None, STREAM_DRAW)
        gl.BindFramebuffer(FRAMEBUFFER, main.fbo)
        gl.Viewport(0, 0, w, h)
        gl.ClearColor(*rgb(scene.get("background", "#000000")), 1)
        gl.Clear(COLOR_BUFFER_BIT | DEPTH_BUFFER_BIT)
        gl.Enable(DEPTH_TEST)
        gl.DepthFunc(LEQUAL)
        gl.Enable(BLEND)
        gl.BlendFunc(ONE, ONE_MINUS_SRC_ALPHA)
        gl.UseProgram(self.lit)
        L = self.loc
        gl.ActiveTexture(TEXTURE0 + 2)
        gl.BindTexture(TEXTURE_BUFFER, self.itex)
        last_mode, last_tex, last_vao = None, None, None  # (what's set already: not set again)
        for vao, count, mode, tex, first, n in runs:
            if mode != last_mode:
                gl.Uniform1i(L["mode"], mode)
                last_mode = mode
            if mode and tex != last_tex:
                gl.ActiveTexture(TEXTURE0 + (1 if mode == 2 else 0))
                gl.BindTexture(TEXTURE_2D_ARRAY if mode == 2 else TEXTURE_2D, tex)
                last_tex = tex
            if vao != last_vao:
                gl.BindVertexArray(vao)
                last_vao = vao
            gl.Uniform1i(L["base"], first)
            gl.DrawArraysInstanced(TRIANGLES, 0, count, n)
        gl.ActiveTexture(TEXTURE0)
        gl.Disable(DEPTH_TEST)
        gl.Disable(BLEND)
        bloom = scene.get("bloom")
        gl.BindVertexArray(self.quad[0])
        if bloom:
            gl.BindFramebuffer(FRAMEBUFFER, half_a.fbo)
            gl.Viewport(0, 0, half_a.w, half_a.h)
            gl.UseProgram(self.bright)
            gl.Uniform1f(self.bright_loc[0], float(bloom.get("threshold", 1.0)))
            gl.Uniform1i(self.bright_loc[1], 0)
            gl.BindTexture(TEXTURE_2D, main.tex)
            gl.DrawArrays(TRIANGLE_STRIP, 0, 4)
            gl.UseProgram(self.blur)
            gl.Uniform1i(self.blur_loc[1], 0)
            spread = max(0.5, float(bloom.get("radius", 4)) / 4)
            for _ in range(3):
                for src, dst, d in ((half_a, half_b, (spread / half_a.w, 0)), (half_b, half_a, (0, spread / half_a.h))):
                    gl.BindFramebuffer(FRAMEBUFFER, dst.fbo)
                    gl.Uniform2f(self.blur_loc[0], *d)
                    gl.BindTexture(TEXTURE_2D, src.tex)
                    gl.DrawArrays(TRIANGLE_STRIP, 0, 4)
        gl.BindFramebuffer(FRAMEBUFFER, out.value)
        gl.Viewport(0, 0, w, h)
        gl.UseProgram(self.compose)
        gl.Uniform1i(self.compose_loc[0], 0)
        gl.Uniform1i(self.compose_loc[1], 1)
        gl.Uniform1f(self.compose_loc[2], float((bloom or {}).get("strength", 0.0)))
        gl.ActiveTexture(TEXTURE0)
        gl.BindTexture(TEXTURE_2D, main.tex)
        gl.ActiveTexture(TEXTURE0 + 1)
        gl.BindTexture(TEXTURE_2D, half_a.tex)
        gl.DrawArrays(TRIANGLE_STRIP, 0, 4)
        gl.ActiveTexture(TEXTURE0)
