"""The Christmas scene's desktop code: a present opened lifts its lid and its letter rises out of it, and the view
goes to it; the dashboard, a host's place, the room: a click goes to each. The tree lights twinkle, the candle and the
fire flicker, a present whose chat asks something pulses, and each host's tree has a light of its own. Given the
scene's SceneAPI and nothing else (scenehost.SceneAPI); run only once trusted. Without it the scene's as scene.json
has it: a click opens a present into its letter just the same."""
import math

from gi.repository import Graphene

RISE = 0.9  # (s: a letter out of its box)
HELD = (0.0, 1.2, 1.35)  # (where a letter's held up to be read: in front of its tree, in the tree's space)
SEEN = 2.0  # (m: the view from a letter, as it goes to one)


def setup(scene):
    cam = scene.camera
    home = {"position": list(cam["position"]), "target": list(cam["target"]), "orbit": cam.get("orbit")}
    s = {"goal": None, "opening": {}, "own": len(scene.lights)}
    mats = scene.materials
    glows = {k: mats[k].get("glow", 1.0) for k in ("bulb_warm", "bulb_red", "star", "ribbon_ask", "fire", "flame")}
    flicker = {i: dict(scene.lights[i]) for i in (1, 2)}

    def at(nid, x=0.0, y=0.0, z=0.0):
        """A point in a node's space, in the world's."""
        m = Graphene.Matrix().init_identity()
        n = scene.node(nid)
        while n is not None:
            m = m.multiply(n.local())
            n = scene.node(n.parent) if n.parent else None
        p = m.transform_point3d(Graphene.Point3D().init(x, y, z))
        return [p.x, p.y, p.z]

    def into(nid, upto, p):
        """A point in upto's space, in nid's (nid in upto)."""
        m = Graphene.Matrix().init_identity()
        n = scene.node(nid)
        while n.id != upto:
            m = m.multiply(n.local())
            n = scene.node(n.parent)
        ok, inv = m.inverse()
        q = inv.transform_point3d(Graphene.Point3D().init(*p))
        return [q.x, q.y, q.z]

    def look(target, frm):
        cam.pop("orbit", None)
        s["goal"] = (frm, target)

    def trees():  # (each host that's up: a light by its tree, as many as the scene has room for)
        del scene.lights[s["own"]:]
        for n in scene.nodes():
            if n.id.endswith("/tree") and len(scene.lights) < 8:
                host = next((o for o in scene.world if o["id"] == (scene.node(n.parent).obj or {}).get("id")), None)
                if host is not None and host.get("state") == "up":
                    x, y, z = at(n.id, 0, 1.1, 0.9)
                    scene.lights.append({"type": "point", "position": [x, y, z], "color": "#ffbe6a", "intensity": 0.75,
                                         "range": 3.2})

    def picked(n, obj):
        nid = n.id
        if nid.endswith("/box"):  # (a present: its lid off and its letter up; the data shows them)
            group = nid.rsplit("/", 1)[0]
            tree = scene.node(scene.node(group).parent).parent
            s["opening"][group] = (scene.time, into(group, tree, HELD), scene.node(group).rotation[1])
            scene.node(group + "/panel").position = [0, -0.85, 0]
            scene.node(group + "/panel").scale = [0.3, 0.3, 1]
            scene.node(group + "/lid").rotation, scene.node(group + "/lid").position = [0, 0, 0], [0, 0.17, 0]
            look(at(tree, *HELD), at(tree, HELD[0], HELD[1] + 0.2, HELD[2] + SEEN))
            return False
        if nid.endswith("/base"):  # (an open present clicked again: wrapped up)
            group = nid.rsplit("/", 2)[0]
            s["opening"].pop(group, None)
            panel = scene.node(group + "/panel")
            panel.position, panel.rotation, panel.scale = [0, 0, 0], [0, 0, 0], [1, 1, 1]
            scene.show(group + "/box")
            scene.show(group + "/open", False)
            scene.show(group + "/panel", False)
            return True
        if nid.endswith("/rug"):
            tree = nid.rsplit("/", 1)[0] + "/tree"
            look(at(tree, 0, 0.75, 0.3), at(tree, 0, 1.35, 4.4))
            return True
        if nid == "backing":
            look(at("backing"), [p + d for p, d in zip(at("backing"), (0, 0.0, 3.4))])
            return True
        if nid.startswith(("floor", "wall")) or nid == "desk":
            s["goal"] = (home["position"], home["target"])
            if home["orbit"] is not None:
                cam["orbit"] = home["orbit"]
            return True
        return False

    def frame(t, dt):
        for group, (t0, held, turn) in list(s["opening"].items()):
            p = min(1.0, (t - t0) / RISE)
            e = 1 - (1 - p) ** 3
            try:
                panel, lid = scene.node(group + "/panel"), scene.node(group + "/lid")
            except KeyError:
                s["opening"].pop(group)
                continue
            panel.position = [held[0] * e, -0.85 + (held[1] + 0.85) * e, held[2] * e]
            panel.rotation = [0, -turn * e, 0]
            panel.scale = [0.3 + 0.7 * e, 0.3 + 0.7 * e, 1]
            lid.rotation = [-72 * e, 0, 0]
            lid.position = [0, 0.17 - 0.15 * e, -0.32 * e]
            if p >= 1:
                s["opening"].pop(group)
        if s["goal"] is not None:
            frm, target = s["goal"]
            k = 1 - math.exp(-dt * 4)
            cam["position"] = [a + (b - a) * k for a, b in zip(cam["position"], frm)]
            cam["target"] = [a + (b - a) * k for a, b in zip(cam["target"], target)]
            if max(abs(a - b) for a, b in zip(cam["position"] + cam["target"], frm + target)) < 1e-3:
                s["goal"] = None
        tw = 0.5 + 0.5 * math.sin(t * 2.7)
        mats["bulb_warm"]["glow"] = glows["bulb_warm"] * (0.75 + 0.3 * tw)
        mats["bulb_red"]["glow"] = glows["bulb_red"] * (1.05 - 0.3 * tw)
        mats["star"]["glow"] = glows["star"] * (0.9 + 0.12 * math.sin(t * 1.3))
        mats["ribbon_ask"]["glow"] = glows["ribbon_ask"] * (0.7 + 0.45 * (0.5 + 0.5 * math.sin(t * 3.4)))
        f = 0.85 + 0.09 * math.sin(t * 11.3) + 0.06 * math.sin(t * 17.9 + 1.3)
        scene.lights[1]["intensity"] = flicker[1]["intensity"] * f
        mats["flame"]["glow"] = glows["flame"] * f
        g = 0.8 + 0.12 * math.sin(t * 7.1) + 0.08 * math.sin(t * 13.7 + 0.4)
        scene.lights[2]["intensity"] = flicker[2]["intensity"] * g
        mats["fire"]["glow"] = glows["fire"] * g

    trees()
    scene.on_world(lambda _things: trees())
    scene.on_pick(picked)
    scene.on_frame(frame)
