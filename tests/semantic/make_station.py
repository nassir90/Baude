"""Writes tests/semantic/space-station/scene.json: the Space Station scene theme's data, worked out once (its ring's
segments, its starfield) so the theme itself is data alone. python3 tests/semantic/make_station.py"""
import json
import math
import os
import random

HERE = os.path.dirname(os.path.abspath(__file__))
R = 4.0  # (the ring's radius, metres)
SEGMENTS = 20


def r3(v):
    return [round(x, 3) for x in v]


def at(angle, radius, y=0.0):
    a = math.radians(angle)
    return r3([radius * math.sin(a), y, radius * math.cos(a)])


def ring():
    out, seg = [], 2 * math.pi * R / SEGMENTS
    for i in range(SEGMENTS):
        a = 360 * i / SEGMENTS
        out.append({"id": f"ring/{i}", "mesh": "box", "model": "corridor_window.glb", "material": "module",
                    "position": at(a, R), "rotation": [0, a + 90, 0], "scale": r3([0.62, 0.5, seg * 1.04]), "pick": "none"})
        out.append({"id": f"windows/{i}", "mesh": "box", "material": "window", "position": at(a, R + 0.315, 0.04),
                    "rotation": [0, a, 0], "scale": r3([seg * 0.7, 0.1, 0.02]), "pick": "none"})
    for i, a in enumerate((60, 140, 220, 300)):
        out.append({"id": f"spoke/{i}", "mesh": "box", "material": "hull_dark", "position": at(a, (R + 0.8) / 2),
                    "rotation": [0, a, 0], "scale": [0.16, 0.16, round(R - 0.9, 3)], "pick": "none"})
    return out


def hub():
    return [
        {"id": "hub", "mesh": "cylinder", "model": "hangar_roundGlass.glb", "material": "module", "scale": [1.9, 1.0, 1.65],
         "pick": "none"},
        {"id": "hub/band", "mesh": "cylinder", "material": "accent", "position": [0, 0.1, 0], "scale": [1.72, 0.16, 1.72], "pick": "none"},
        {"id": "hub/dome", "mesh": "sphere", "material": "glass", "position": [0, 0.55, 0], "scale": [1.3, 0.8, 1.3], "pick": "none"},
        {"id": "mast", "mesh": "cylinder", "material": "hull_dark", "position": [0, 2.1, 0], "scale": [0.09, 3.0, 0.09], "pick": "none"},
        {"id": "beacon", "mesh": "sphere", "material": "beacon", "position": [0, 3.68, 0], "scale": [0.16, 0.16, 0.16], "pick": "none"},
        {"id": "truss", "mesh": "box", "material": "hull_dark", "position": [0, 3.55, -0.6], "scale": [10.4, 0.07, 0.07], "pick": "none"},
        {"id": "truss/post", "mesh": "box", "material": "hull_dark", "position": [0, 3.55, -0.3], "scale": [0.07, 0.07, 0.6], "pick": "none"},
    ]


def solar():
    out = []
    for side in (-1, 1):
        for k, x in enumerate((2.2, 3.85)):
            out.append({"id": f"solar/{'l' if side < 0 else 'r'}{k}", "mesh": "box", "material": "solar",
                        "position": [side * (x + 0.75), 3.55, -0.6], "rotation": [-62, 0, 0],
                        "scale": [1.55, 1.0, 0.03], "pick": "none"})
    return out


def modules():
    """A lab module on the ring's left, a dock on its right, the lamps a pilot flies by."""
    return [
        {"id": "lab", "mesh": "box", "model": "hangar_smallA.glb", "material": "module", "position": [-R - 0.55, 0.0, 0.6],
         "rotation": [0, -14, 0], "scale": [1.3, 0.7, 1.3], "pick": "none"},
        {"id": "lab/light", "mesh": "box", "material": "window", "position": [-R - 0.55, 0.0, 1.26], "rotation": [0, -14, 0],
         "scale": [0.6, 0.08, 0.02], "pick": "none"},
        {"id": "dock", "mesh": "cylinder", "material": "hull", "position": [R + 0.9, 0.0, 0.0], "rotation": [0, 0, 90],
         "scale": [0.6, 1.4, 0.6], "pick": "none"},
        {"id": "dock/craft", "mesh": "box", "model": "craft_cargoA.glb", "material": "module", "position": [R + 2.6, -0.9, -1.6],
         "rotation": [8, -70, 4], "scale": [1.1, 0.55, 1.0], "pick": "none"},
        {"id": "dish", "mesh": "box", "model": "satelliteDish_large.glb", "material": "module", "position": [1.2, 3.62, -0.6],
         "rotation": [0, 200, 0], "scale": [0.55, 0.53, 0.5], "pick": "none"},
        {"id": "dock/port", "mesh": "cylinder", "material": "accent", "position": [R + 1.65, 0.0, 0.0], "rotation": [0, 0, 90],
         "scale": [0.7, 0.12, 0.7], "pick": "none"},
        {"id": "nav/port", "mesh": "sphere", "material": "nav_red", "position": [-R - 1.05, 0.42, 0.6], "scale": [0.12, 0.12, 0.12], "pick": "none"},
        {"id": "nav/starboard", "mesh": "sphere", "material": "nav_green", "position": [R + 1.78, 0.0, 0.0], "scale": [0.12, 0.12, 0.12], "pick": "none"},
        {"id": "lamp", "mesh": "sphere", "material": "lamp", "position": [-2.6, 3.9, 5.0], "scale": [0.13, 0.13, 0.13], "pick": "none"},
        {"id": "planet", "mesh": "sphere", "material": "planet", "position": [34, -30, -60], "scale": [44, 44, 44], "pick": "none"},
    ]


def stars(n=110, seed=7):
    rnd, out = random.Random(seed), []
    for i in range(n):
        while True:  # (on a shell round the station, behind it and above: the camera looks along -z)
            v = [rnd.uniform(-1, 1), rnd.uniform(-0.35, 1), rnd.uniform(-1, 0.15)]
            ln = math.sqrt(sum(c * c for c in v))
            if 0.3 < ln <= 1:
                break
        d = rnd.uniform(70, 95)
        p = [c / ln * d for c in v]
        if p[0] > 0 and p[1] < 0 and p[2] < -20:  # (not where the planet is)
            continue
        bright = i % 9 == 0
        s = rnd.uniform(0.25, 0.4) if not bright else 0.5
        out.append({"id": f"star/{i}", "mesh": "box", "material": "star_bright" if bright else "star", "position": r3(p),
                    "scale": [round(s, 3)] * 3, "pick": "none"})
    return out


STAND = {"id": "stand", "mesh": "box", "material": "frame", "pick": "none"}


def panels():
    """Where Baude's own things go: the chats on the ring's front deck, the sessions list on the left pylon, Meta's
    cards on the right, the host on the lab module, a window (usage) on the dock."""
    chat_w, chat_h = 1.3, 1.23
    row_w, row_h = 1.25, 0.167
    card_w, card_h = 1.2, 0.53
    return [
        {"id": "chats", "position": [0, 1.4, R - 0.1], "rotation": [-5, 0, 0], "material": "panel",
         "slots": {"of": "chat", "sort": "recent", "limit": 3, "layout": "row", "spacing": 0.16,
                   "panel": {"show": "chat", "px": [380, 360], "size": [chat_w, chat_h]},
                   "with": [{**STAND, "id": "frame", "position": [0, 0, -0.035], "scale": [chat_w + 0.07, chat_h + 0.07, 0.04],
                             "states": {"working": "status_working", "waiting": "status_waiting"}},
                            {**STAND, "id": "leg", "position": [0, -chat_h / 2 - 0.27, -0.05], "scale": [0.06, 0.54, 0.06]}]}},
        {"id": "sessions", "position": [-3.75, 2.55, 2.2], "rotation": [0, 30, 0], "material": "panel",
         "slots": {"of": "chat", "sort": "recent", "limit": 6, "layout": "grid", "columns": 1, "spacing": 0.0,
                   "panel": {"show": "chat_row", "px": [300, 40], "size": [row_w, row_h]},
                   "with": [{**STAND, "id": "back", "position": [0, 0, -0.03], "scale": [row_w + 0.07, row_h + 0.002, 0.04]},
                            {"id": "glow", "mesh": "box", "material": "frame", "position": [-row_w / 2 - 0.05, 0, 0],
                             "scale": [0.025, row_h * 0.7, 0.025], "pick": "none",
                             "states": {"working": "status_working", "waiting": "status_waiting"}}]}},
        {"id": "sessions/pylon", "mesh": "cylinder", "material": "hull_dark", "position": [-3.75, 0.85, 2.2],
         "scale": [0.08, 1.2, 0.08], "pick": "none"},
        {"id": "meta", "position": [3.75, 2.85, 2.2], "rotation": [0, -30, 0], "material": "panel",
         "slots": {"of": "meta_card", "sort": "recent", "limit": 3, "layout": "grid", "columns": 1, "spacing": 0.08,
                   "panel": {"show": "meta_card", "px": [340, 150], "size": [card_w, card_h]},
                   "with": [{**STAND, "id": "back", "position": [0, 0, -0.03], "scale": [card_w + 0.06, card_h + 0.06, 0.04],
                             "states": {"running": "status_working", "asking": "status_waiting"}}]}},
        {"id": "meta/pylon", "mesh": "cylinder", "material": "hull_dark", "position": [3.75, 0.8, 2.2],
         "scale": [0.08, 1.2, 0.08], "pick": "none"},
        {"id": "hosts", "position": [-R - 0.85, 0.85, 1.1], "rotation": [-4, 38, 0], "material": "panel",
         "slots": {"of": "host", "sort": "name", "limit": 1, "layout": "row",
                   "panel": {"show": "host", "px": [280, 190], "size": [0.95, 0.645]},
                   "with": [{**STAND, "id": "frame", "position": [0, 0, -0.03], "scale": [1.01, 0.705, 0.04],
                             "states": {"up": "status_up", "down": "status_down"}}]}},
        {"id": "windows", "position": [R + 1.15, 0.62, 1.7], "rotation": [-4, -38, 0], "material": "panel",
         "slots": {"of": "window", "sort": "recent", "limit": 1, "layout": "row",
                   "panel": {"show": "window", "px": [420, 170], "size": [1.45, 0.587]},
                   "with": [{**STAND, "id": "frame", "position": [0, 0, -0.03], "scale": [1.51, 0.647, 0.04]}]}},
    ]


MATERIALS = {
    "hull": {"color": "#c9cfd8", "specular": 0.7, "shininess": 72, "texture": "hull_color.jpg",
             "normal_texture": "hull_normal.jpg", "orm_texture": "hull_orm.jpg", "repeat": [2, 1]},
    "module": {"color": "#959dab", "specular": 0.7, "shininess": 72},
    "hull_dark": {"color": "#4a515e", "specular": 0.5, "shininess": 48},
    "accent": {"color": "#c96a2b", "specular": 0.6, "shininess": 64},
    "glass": {"color": "#26384f", "specular": 1.0, "shininess": 128, "emissive": "#0e1a2a"},
    "solar": {"color": "#1d3566", "specular": 0.95, "shininess": 110, "texture": "solar_color.jpg",
              "normal_texture": "solar_normal.jpg", "orm_texture": "solar_orm.jpg", "repeat": [3, 2], "metalness": 0.35,
              "roughness": 0.45},
    "frame": {"color": "#2c313b", "specular": 0.6, "shininess": 80},
    "window": {"color": "#ffd9a0", "emissive": "#ffd08a", "glow": 1.4},
    "beacon": {"color": "#ff4a3a", "emissive": "#ff3b2e", "glow": 3.2},
    "nav_red": {"color": "#ff4a3a", "emissive": "#ff3b2e", "glow": 2.6},
    "nav_green": {"color": "#45ff8a", "emissive": "#3dff7e", "glow": 2.6},
    "lamp": {"color": "#fff1d6", "emissive": "#fff1d6", "glow": 3.0},
    "star": {"color": "#000000", "emissive": "#d8e2ff", "glow": 0.95},
    "star_bright": {"color": "#000000", "emissive": "#eef2ff", "glow": 1.8},
    "planet": {"color": "#3a6ea8", "specular": 0.35, "shininess": 18},
    "panel": {"color": "#3a3a3a", "specular": 0.25, "shininess": 48, "emissive": "#c4c4c4"},
    "status_working": {"color": "#2c313b", "emissive": "#5c8cff", "glow": 1.9},
    "status_waiting": {"color": "#2c313b", "emissive": "#ffb547", "glow": 1.9},
    "status_up": {"color": "#2c313b", "emissive": "#3fd07a", "glow": 1.6},
    "status_down": {"color": "#2c313b", "emissive": "#ff5a4a", "glow": 1.6},
}


def scene():
    return {
        "renderer": "gfx",
        "camera": {"position": [0, 3.3, 11.6], "target": [0, 0.95, 0], "fov": 42, "orbit": {"yaw": 9, "period": 48}},
        "environment": {"texture": "studio_small_09_1k.hdr", "intensity": 0.3},
        "backdrop": {"texture": "stars.jpg", "intensity": 0.35, "instead_of": ["star", "star_bright"]},
        "background": "#03050a",
        "ambient": "#1c2230",
        "lights": [
            {"type": "point", "position": [-2.6, 3.9, 5.0], "color": "#fff1d6", "intensity": 1.3, "range": 16},
            {"type": "directional", "direction": [0.45, -0.5, -0.75], "color": "#fff4e0", "intensity": 0.75},
            {"type": "point", "position": [4.2, 1.2, 4.2], "color": "#8fb6ff", "intensity": 0.7, "range": 9},
        ],
        "bloom": {"threshold": 1.12, "strength": 0.8, "radius": 5},
        "materials": MATERIALS,
        "nodes": hub() + ring() + solar() + modules() + panels() + stars(),
    }


if __name__ == "__main__":
    with open(os.path.join(HERE, "space-station", "scene.json"), "w") as fh:
        json.dump(scene(), fh, indent=1)
        fh.write("\n")
