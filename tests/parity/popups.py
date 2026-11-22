#!/usr/bin/env python3
"""The popups the phone opens (tests/parity/popups.json), as the rig drew them (Parity.kt, Measure.kt): measured and laid
out for a person or an agent to look at.

    python3 tests/parity/popups.py [OUT]

Reads tests/parity/out/popups/ (each scene's PNG, light and dark, and its layout dump) and writes OUT
(tests/parity/out/popups-report/ by default):
  <place>.png, dark/<place>.png   each popup over what it opened from (place: the scene's name less "pop-")
  sheets/<n>-{light,dark}.png     eight places to a sheet, labelled, in popups.json's order
  dumps/<place>.json              the rig's dump: every laid-out node of every window
  measured.json                   per popup: its box, its rows' heights, what starts and ends where, a dialog's parts
  table.md                        one line a popup, in device dp (screen pixels / the screen's density)

Rows are a popup's clickable semantics nodes; a row's left is its box's edge to the first thing drawn in it (a check,
icon, dot or text's ink), its right the last thing drawn to the edge. Nothing's checked."""
import json
import os
import shutil
import statistics
import sys

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "out", "popups")
DEV = 3.0  # (px per device dp: the rig's xxhdpi; each dump says its own)


def dp(px):
    return None if px is None else round(px / DEV, 1)

def R(n, inner=False):
    if inner:
        return n['il'], n['it'], n['il'] + n['iw'], n['it'] + n['ih']
    return n['l'], n['t'], n['l'] + n['w'], n['t'] + n['h']

def has(n, m):
    return any(m in x for x in n['mods'])

def inside(n, box, tol=2):
    l, t, r, b = R(n)
    cx, cy = (l + r) / 2, (t + b) / 2
    return box[0] - tol <= cx <= box[2] + tol and box[1] - tol <= cy <= box[3] + tol

def ink(n):
    """Visible extent: a text's lines, an icon's box, a field's box."""
    if n.get('kind') == 'text' and n.get('tl') is not None:
        return n['tl'], n['it'], n['tr'], n['it'] + n['ih']
    return R(n)

def content(nodes, box, container):
    """Visible things drawn inside box: text, icons, small fills (dots, avatars, switches)."""
    cw = container[2] - container[0]
    out = []
    for n in nodes:
        if n['w'] <= 0 or n['h'] <= 0 or not inside(n, box):
            continue
        k = n.get('kind')
        if k == 'text' and n.get('text', '').strip() and n.get('tr') is not None and n['tr'] - n['tl'] > 1:
            out.append(('text', n))
        elif k == 'icon':
            out.append(('icon', n))
        elif k == 'draw' and n['leaf'] and n['w'] < 0.5 * cw and n['h'] < 0.9 * (box[3] - box[1]) and n['w'] > 6 and n['h'] > 6:
            out.append(('fill', n))
    return out

def container_of(win, measure):
    nodes = win['nodes']
    by = {n['i']: n for n in nodes}
    W = win['w']
    if measure and measure.get('pane'):
        key = measure['pane']
        hit = next((n for n in nodes if key in n.get('text', '') and n['w'] > 0), None)
        if hit is None:
            return None
        n = hit
        while n['parent'] >= 0:
            n = by[n['parent']]
            if has(n, 'Background') and n['w'] < W * (1.01 if measure.get('wide') else 0.98) and n['w'] > 100 and \
                    (not measure.get('wide') or n['w'] > 0.9 * W):
                return n
        return None
    for n in sorted(nodes, key=lambda n: n['depth']):
        if has(n, 'Background') and n['w'] > 60 and n['h'] > 60:
            return n
    return nodes[0] if nodes else None

def stats(v):
    v = [x for x in v if x is not None]
    if not v:
        return None
    return {'typ': round(statistics.median(v), 1), 'min': round(min(v), 1), 'max': round(max(v), 1), 'n': len(v)}

def analyse(path):
    d = json.load(open(path))
    scene = d['scene']
    measure = d.get('measure') or {}
    wins = d['windows']
    if measure.get('pane') or measure.get('full'):
        win = next(w for w in wins if w['activity'])
    else:
        win = [w for w in wins if not w['activity']][-1] if len(wins) > 1 else wins[0]
    cls = win['place'].get('class', '')
    kind = 'pane' if win['activity'] else ('dialog' if 'DecorView' in cls else 'popup')
    res = {'scene': scene, 'what': d.get('what'), 'window': kind, 'class': cls.split('.')[-1]}
    nodes = win['nodes']
    dens = sorted({round(n['density'], 2) for n in nodes if n['depth'] > 2})
    res['density'] = dens
    if measure.get('full'):
        top = [n for n in nodes if n.get('kind') in ('text', 'icon') and n['t'] < 250 and n['w'] > 0]
        res['bar'] = {
            'left': dp(min(ink(n)[0] for n in top)), 'right': dp(win['w'] - max(ink(n)[2] for n in top)),
            'clicks': [{'label': c['text'] or c['desc'], 'h': dp(c['h']), 'l': dp(c['l']), 'r': dp(win['w'] - c['l'] - c['w'])}
                       for c in win['clicks'] if c['t'] < 250 and c['w'] > 0]}
        return res
    c = container_of(win, measure)
    if c is None:
        res['error'] = 'no container'
        return res
    kids = {}
    for n in nodes:
        kids.setdefault(n['parent'], []).append(n['i'])
    under, todo = set(), [c['i']]
    while todo:
        i = todo.pop()
        under.add(i)
        todo.extend(kids.get(i, []))
    nodes = [n for n in nodes if n['i'] in under]
    sems = {n['sem'] for n in nodes}
    win = dict(win, clicks=[k for k in win['clicks'] if k['id'] in sems])
    box = (c['bg'][0], c['bg'][1], c['bg'][0] + c['bg'][2], c['bg'][1] + c['bg'][3]) if c.get('bg') else R(c)
    c = dict(c, l=box[0], t=box[1], w=box[2] - box[0], h=box[3] - box[1])
    res['container'] = {'w': dp(c['w']), 'h': dp(c['h']), 'x': dp(c['l'] + win['place'].get('x', 0)),
                        'y': dp(c['t'] + win['place'].get('y', 0)), 'win_w': dp(win['w'])}
    clicks = [k for k in win['clicks'] if k['w'] > 0 and k['h'] > 0 and inside({'l': k['l'], 't': k['t'], 'w': k['w'], 'h': k['h']}, box)]
    fields = [k for k in clicks if k['edit']]
    buttons = [k for k in clicks if not k['edit']]
    texts = [n for n in nodes if n.get('kind') == 'text' and inside(n, box) and n.get('text', '').strip() and n['w'] > 0 and n.get('tl') is not None]
    res['fonts'] = sorted({round(n['fontPx'] / DEV, 1) for n in texts if n.get('fontPx')})
    def rowinfo(k):
        rb = (k['l'], k['t'], k['l'] + k['w'], k['t'] + k['h'])
        cont = content(nodes, rb, box)
        cont = [x for x in cont if not (x[0] == 'fill' and R(x[1])[2] - R(x[1])[0] > 0.8 * (rb[2] - rb[0]))]
        cont.sort(key=lambda x: ink(x[1])[0])
        info = {'label': (k['text'] or k['desc'])[:40], 'h': dp(k['h']), 'w': dp(k['w']),
                'inset_l': dp(k['l'] - box[0]), 'inset_r': dp(box[2] - k['l'] - k['w'])}
        if cont:
            info['left'] = dp(ink(cont[0][1])[0] - box[0])
            info['right'] = dp(box[2] - max(ink(x[1])[2] for x in cont))
            info['first'] = cont[0][0]
            info['last'] = max(cont, key=lambda x: ink(x[1])[2])[0]
            tx = [x for x in cont if x[0] == 'text']
            if tx:
                info['text_l'] = dp(ink(tx[0][1])[0] - box[0])
                info['text_r'] = dp(box[2] - ink(tx[-1][1])[2])
                info['font'] = dp(tx[0][1].get('fontPx', 0))
            if len(cont) > 1 and cont[0][0] in ('icon', 'fill') and cont[1][0] == 'text':
                info['gap'] = dp(ink(cont[1][1])[0] - ink(cont[0][1])[2])
            ics = [x for x in cont if x[0] == 'icon']
            if ics:
                info['icon'] = dp(ics[0][1]['w'])
            last = cont[-1]
            if last[0] == 'icon' and len(cont) > 1 and tx:
                info['trail_gap'] = dp(ink(last[1])[0] - ink(tx[-1][1])[2])
                info['trail_r'] = dp(box[2] - ink(last[1])[2])
        return info
    rows = [rowinfo(k) for k in sorted(buttons, key=lambda k: (k['t'], k['l']))]
    res['rows'] = rows
    # headings / loose text not in any clickable row
    def in_click(n):
        return any(inside(n, (k['l'], k['t'], k['l'] + k['w'], k['t'] + k['h'])) for k in clicks)
    loose = [n for n in texts if not in_click(n)]
    res['loose'] = [{'text': n['text'][:40], 'l': dp(n['tl'] - box[0]), 'r': dp(box[2] - n['tr']), 'top': dp(n['it'] - box[1]),
                     'h': dp(n['h']), 'font': dp(n.get('fontPx', 0))} for n in sorted(loose, key=lambda n: n['t'])]
    res['fields'] = [{'label': f['text'][:30], 'l': dp(f['l'] - box[0]), 'r': dp(box[2] - f['l'] - f['w']), 'h': dp(f['h'])} for f in fields]
    # field text inset (placeholder/value text inside the field box), and the field's drawn box
    for f, fi in zip(fields, res['fields']):
        fb = (f['l'], f['t'], f['l'] + f['w'], f['t'] + f['h'])
        boxes = [n['bg'] for n in nodes if n.get('bg') and n['bg'][0] <= fb[0] + 1 and n['bg'][1] <= fb[1] + 1 and
                 n['bg'][0] + n['bg'][2] >= fb[2] - 1 and n['bg'][1] + n['bg'][3] >= fb[3] - 1 and n['bg'][2] < 0.99 * (box[2] - box[0])]
        if boxes:
            b = min(boxes, key=lambda b: b[2] * b[3])
            fi['box_l'], fi['box_r'], fi['box_h'] = dp(b[0] - box[0]), dp(box[2] - b[0] - b[2]), dp(b[3])
            fi['text_in_box'] = dp(f['l'] - b[0])
            fb = (b[0], b[1], b[0] + b[2], b[1] + b[3])
        inner = [n for n in texts if inside(n, fb)]
        if inner:
            fi['text_inset'] = dp(min(n['tl'] for n in inner) - f['l'])
    # all visible content's outer edges
    allc = content(nodes, box, box)
    allc = [x for x in allc if not (x[0] == 'fill' and x[1]['w'] > 0.8 * c['w'])]
    edges = [ink(x[1]) for x in allc] + [(f['l'], f['t'], f['l'] + f['w'], f['t'] + f['h']) for f in fields]
    if edges:
        res['content_l'] = dp(min(e[0] for e in edges) - box[0])
        res['content_r'] = dp(box[2] - max(e[2] for e in edges))
        res['content_t'] = dp(min(e[1] for e in edges) - box[1])
        res['content_b'] = dp(box[3] - max(e[3] for e in edges))
    if kind == 'dialog':
        title = min(texts, key=lambda n: n['t']) if texts else None
        bt = [k for k in buttons if k['h'] < 80 * DEV]
        bottom = max((k['t'] for k in bt), default=None)
        brow = [k for k in bt if bottom is not None and abs(k['t'] - bottom) < 10]
        brow.sort(key=lambda k: k['l'])
        res['dialog'] = {
            'title': title['text'][:50] if title else None,
            'title_l': dp(title['tl'] - box[0]) if title else None,
            'title_top': dp(title['it'] - box[1]) if title else None,
            'title_font': dp(title.get('fontPx', 0)) if title else None,
            'buttons': [b['text'] for b in brow],
            'buttons_r': dp(box[2] - brow[-1]['l'] - brow[-1]['w']) if brow else None,
            'buttons_b': dp(box[3] - brow[-1]['t'] - brow[-1]['h']) if brow else None,
            'button_h': dp(brow[-1]['h']) if brow else None,
            'button_gap': dp(brow[-1]['l'] - brow[-2]['l'] - brow[-2]['w']) if len(brow) > 1 else None,
        }
        body = [n for n in texts if title is not None and n is not title and not in_click(n)]
        res['dialog']['body_l'] = dp(min(n['tl'] for n in body) - box[0]) if body else None
    hs = [r['h'] for r in rows]
    res['row_h'] = stats(hs)
    res['left'] = stats([r.get('left') for r in rows])
    res['text_l'] = stats([r.get('text_l') for r in rows])
    res['right'] = stats([r.get('right') for r in rows])
    res['gap'] = stats([r.get('gap') for r in rows])
    res['trail_r'] = stats([r.get('trail_r') for r in rows])
    return res


def sheet(out, files, cols=4, cw=400):
    ims = [Image.open(f).convert("RGB") for f in files]
    ch = max(int(i.height * cw / i.width) for i in ims)
    lab = 34
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 24)
    except OSError:
        font = ImageFont.load_default()
    rows = (len(ims) + cols - 1) // cols
    s = Image.new("RGB", (cols * (cw + 16) + 16, rows * (ch + lab + 16) + 16), "white")
    d = ImageDraw.Draw(s)
    for k, (f, im) in enumerate(zip(files, ims)):
        r, c = divmod(k, cols)
        x, y = 16 + c * (cw + 16), 16 + r * (ch + lab + 16)
        d.text((x, y), os.path.splitext(os.path.basename(f))[0], fill="black", font=font)
        t = im.resize((cw, int(im.height * cw / im.width)), Image.LANCZOS)
        s.paste(t, (x, y + lab))
        d.rectangle([x - 1, y + lab - 1, x + cw, y + lab + t.height], outline="#999")
    s.save(out)


def rng(s):
    if not s:
        return "–"
    return f"{s['typ']:g}" if s["min"] == s["max"] else f"{s['typ']:g} ({s['min']:g}–{s['max']:g})"


def table(scenes, measured):
    lines = ["| place | where it opens from | kind | rows | row height (typical, min–max) | left | right | icon–text gap | box |",
             "|---|---|---|---|---|---|---|---|---|"]
    for sc in scenes:
        p = sc["name"][4:]
        r = measured.get(p)
        if r is None:
            lines.append(f"| {p} | {sc.get('where', '')} | {sc.get('kind', '')} | not drawn | | | | | |")
            continue
        head = f"| {p} | {sc.get('where', '')} | {sc.get('kind', '')} |"
        if r.get("window") == "dialog" and r.get("dialog"):
            d = r["dialog"]
            fl = [f for f in r.get("fields", []) if f.get("box_l") is not None]
            fields = f"; field boxes {fl[0]['box_l']:g}/{fl[0]['box_r']:g}, text {fl[0]['text_in_box']:g} in" if fl else ""
            lines.append(f"{head} – | buttons {d['button_h']} | {r.get('content_l')} (title {d['title_l']}, body {d['body_l']}) | "
                         f"{d['buttons_r']} to buttons{fields} | – | {r['container']['w']:g} wide at x {r['container']['x']:g}; "
                         f"title {d['title_font']} at top {d['title_top']}; buttons {d['button_gap']} apart, {d['buttons_b']} from the bottom |")
            continue
        if "bar" in r or not r.get("rows"):
            lines.append(f"{head} – | – | – | – | – | {r.get('error', '')} |")
            continue
        trail = r.get("trail_r")
        right = f"{rng(trail)} to tick/chevron; {r['right']['min']:g} widest" if trail else f"{r['right']['min']:g} (widest text)"
        left = rng(r["left"])
        if r.get("text_l") and r["text_l"] != r["left"]:
            left += f"; text {rng(r['text_l'])}"
        c = r["container"]
        lines.append(f"{head} {len(r['rows'])} | {rng(r['row_h'])} | {left} | {right} | {rng(r['gap'])} | "
                     f"{c['w']:g} × {c['h']:g} at {c['x']:g}, {c['y']:g}; density {r['density'][0] if r['density'] else '–'} |")
    return "\n".join(lines) + "\n"


def main():
    global DEV
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "out", "popups-report")
    with open(os.path.join(HERE, "popups.json")) as f:
        scenes = json.load(f)["scenes"]
    for sub in ("dark", "sheets", "dumps"):
        os.makedirs(os.path.join(out, sub), exist_ok=True)
    measured = {}
    for sc in scenes:
        name, place = sc["name"], sc["name"][4:]
        for theme, dest in (("light", out), ("dark", os.path.join(out, "dark"))):
            png = os.path.join(SRC, theme, f"{name}.png")
            if os.path.exists(png):
                shutil.copy(png, os.path.join(dest, f"{place}.png"))
        dump = os.path.join(SRC, f"{name}.json")
        if os.path.exists(dump):
            shutil.copy(dump, os.path.join(out, "dumps", f"{place}.json"))
            with open(dump) as f:
                DEV = json.load(f).get("deviceDensity") or DEV
            r = analyse(dump)
            r["scene"] = place
            measured[place] = r
    with open(os.path.join(out, "measured.json"), "w") as f:
        json.dump([measured[s["name"][4:]] for s in scenes if s["name"][4:] in measured], f, indent=1, ensure_ascii=False)
    places = [s["name"][4:] for s in scenes if os.path.exists(os.path.join(out, s["name"][4:] + ".png"))]
    for i in range(0, len(places), 8):
        for theme, base in (("light", out), ("dark", os.path.join(out, "dark"))):
            files = [os.path.join(base, f"{p}.png") for p in places[i:i + 8] if os.path.exists(os.path.join(base, f"{p}.png"))]
            if files:
                sheet(os.path.join(out, "sheets", f"{i // 8 + 1}-{theme}.png"), files)
    with open(os.path.join(out, "table.md"), "w") as f:
        f.write(table(scenes, measured))
    print(f"{len(places)} popups: {out}")


if __name__ == "__main__":
    main()
