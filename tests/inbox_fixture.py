"""Pictures for the Inbox's scenes (tests/scenes_inbox.py): made-up sites, logos and charts, drawn with cairo. Fixture
data only."""
import math
import os

import cairo

SANS = "Helvetica Neue"


def rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


SCALE = 2  # drawn at twice the size: the full-width ones are shown over 1000 px wide


def canvas(w, h, bg="#ffffff"):
    s = cairo.ImageSurface(cairo.FORMAT_ARGB32, int(w * SCALE), int(h * SCALE))
    c = cairo.Context(s)
    c.scale(SCALE, SCALE)
    if bg:
        c.set_source_rgb(*rgb(bg))
        c.paint()
    return s, c


def rrect(c, x, y, w, h, r):
    c.new_sub_path()
    c.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    c.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    c.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    c.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    c.close_path()


def fill(c, col, a=1.0):
    c.set_source_rgba(*rgb(col), a)
    c.fill()


def box(c, x, y, w, h, col, r=0):
    if r:
        rrect(c, x, y, w, h, r)
    else:
        c.rectangle(x, y, w, h)
    fill(c, col)


def text(c, x, y, s, size, col="#222222", bold=False, font=SANS, anchor="left"):
    c.select_font_face(font, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD if bold else cairo.FONT_WEIGHT_NORMAL)
    c.set_font_size(size)
    ext = c.text_extents(s)
    if anchor == "center":
        x -= ext.x_advance / 2
    elif anchor == "right":
        x -= ext.x_advance
    c.move_to(x, y)
    c.set_source_rgb(*rgb(col))
    c.show_text(s)
    return ext.x_advance


def lines(c, x, y, widths, col="#e3e7ec", h=10, gap=20):
    for i, w in enumerate(widths):
        box(c, x, y + i * gap, w, h, col, 4)


# ---------------------------------------------------------------- 1. a tea shop's home page, before and after
def tea_home(path, after):
    W, H = 960, 600
    s, c = canvas(W, H, "#fbf8f3" if after else "#ffffff")
    nav = "#2f4a3a" if after else "#7a4b2a"
    box(c, 0, 0, W, 64, nav)
    # leaf mark
    c.save()
    c.translate(34, 32)
    c.rotate(-0.6)
    c.scale(1, 0.55)
    c.arc(0, 0, 14, 0, 2 * math.pi)
    c.restore()
    fill(c, "#b9d8a6" if after else "#f0c48a")
    text(c, 58, 41, "Northwind Tea", 24, "#ffffff", True)
    nav_items = ["Shop", "Subscriptions", "Journal", "Cart (2)"] if after else ["Shop", "About", "Journal", "Cart (2)"]
    x = W - 30
    for t in reversed(nav_items):
        x -= text(c, -1000, -1000, t, 17) + 26
        text(c, x, 39, t, 17, "#ffffff")
    if after:
        # hero with a photo-like block on the right
        text(c, 56, 160, "Fresh leaf, every month", 38, "#1f2d25", True)
        text(c, 56, 205, "Single-estate teas picked this season,", 21, "#5b6660")
        text(c, 56, 233, "shipped the week they arrive.", 21, "#5b6660")
        box(c, 56, 268, 210, 54, "#3f7d57", 27)
        text(c, 161, 302, "Start a box", 20, "#ffffff", True, anchor="center")
        box(c, 284, 268, 170, 54, "#e6ece7", 27)
        text(c, 369, 302, "Shop all", 20, "#2f4a3a", True, anchor="center")
        g = cairo.LinearGradient(560, 100, 900, 340)
        g.add_color_stop_rgb(0, *rgb("#9cc58a"))
        g.add_color_stop_rgb(1, *rgb("#3f7d57"))
        rrect(c, 560, 96, 344, 236, 18)
        c.set_source(g)
        c.fill()
        c.arc(732, 214, 70, 0, 2 * math.pi)
        fill(c, "#f4efe4")
        c.arc(732, 214, 52, 0, 2 * math.pi)
        fill(c, "#c78a3b", 0.85)
        names = [("Sencha", "£9"), ("Darjeeling", "£11"), ("Oolong", "£12"), ("Rooibos", "£8")]
    else:
        g = cairo.LinearGradient(0, 64, 0, 340)
        g.add_color_stop_rgb(0, *rgb("#e9d6c1"))
        g.add_color_stop_rgb(1, *rgb("#f6ede3"))
        c.rectangle(0, 64, W, 276)
        c.set_source(g)
        c.fill()
        text(c, W / 2, 170, "Welcome to Northwind Tea", 42, "#4a2d18", True, anchor="center")
        text(c, W / 2, 214, "Loose leaf tea since 2009.", 21, "#7a5a44", anchor="center")
        box(c, W / 2 - 100, 244, 200, 52, "#7a4b2a", 6)
        text(c, W / 2, 277, "Shop now", 20, "#ffffff", True, anchor="center")
        names = [("Sencha", "£9"), ("Darjeeling", "£11"), ("Oolong", "£12"), ("Rooibos", "£8")]
    # product cards
    cw = (W - 56 * 2 - 3 * 20) / 4
    tints = ["#a9c99a", "#d9a46b", "#c9b27c", "#c4623f"]
    for i, (n, p) in enumerate(names):
        x = 56 + i * (cw + 20)
        y = 372
        box(c, x, y, cw, 200, "#ffffff", 12)
        rrect(c, x, y, cw, 200, 12)
        c.set_source_rgb(*rgb("#e2ddd5"))
        c.set_line_width(1.5)
        c.stroke()
        box(c, x + 12, y + 12, cw - 24, 110, tints[i], 8)
        c.arc(x + cw / 2, y + 67, 30, 0, 2 * math.pi)
        fill(c, "#ffffff", 0.35)
        text(c, x + 14, y + 150, n, 18, "#2a2a2a", True)
        text(c, x + 14, y + 178, p + " / 100 g", 16, "#3f7d57" if after else "#7a4b2a")
        if after and i == 1:
            box(c, x + cw - 70, y + 20, 54, 24, "#2f4a3a", 12)
            text(c, x + cw - 43, y + 37, "NEW", 13, "#ffffff", True, anchor="center")
    s.write_to_png(path)


# ---------------------------------------------------------------- 2. a dashboard, before and after a pull request
def dashboard(path, after):
    W, H = 960, 600
    s, c = canvas(W, H, "#f5f6f8")
    box(c, 0, 0, 200, H, "#1e2533")
    text(c, 24, 42, "Tally", 24, "#ffffff", True)
    for i, t in enumerate(["Overview", "Orders", "Customers", "Reports", "Settings"]):
        y = 92 + i * 44
        if i == 0:
            box(c, 12, y - 26, 176, 38, "#2f3a50", 8)
        text(c, 28, y, t, 17, "#ffffff" if i == 0 else "#9aa4b8")
    text(c, 232, 52, "Overview", 28, "#1e2533", True)
    text(c, W - 32, 50, "Last 7 days", 16, "#6b7385", anchor="right")
    cards = [("Revenue", "£18,420", "+12%"), ("Orders", "1,204", "+4%"), ("Refunds", "23", "-9%")]
    cw = (W - 232 - 32 - 2 * 18) / 3
    for i, (k, v, d) in enumerate(cards):
        x = 232 + i * (cw + 18)
        box(c, x, 80, cw, 110, "#ffffff", 12)
        text(c, x + 20, 112, k, 16, "#6b7385")
        text(c, x + 20, 156, v, 30, "#1e2533", True)
        text(c, x + cw - 20, 156, d, 16, "#1f9d63" if d.startswith("+") else "#c0392b", True, anchor="right")
    box(c, 232, 210, W - 264, 360, "#ffffff", 12)
    if after:
        text(c, 256, 246, "Orders per day", 18, "#1e2533", True)
        vals = [148, 162, 155, 190, 210, 176, 163]
        days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        x0, y0, ch, cw2 = 270, 530, 240, (W - 264 - 80) / 7
        for k in range(5):
            y = y0 - k * ch / 4
            c.move_to(x0 - 10, y)
            c.line_to(W - 56, y)
            c.set_source_rgb(*rgb("#eceef2"))
            c.set_line_width(1)
            c.stroke()
            text(c, x0 - 16, y + 5, str(k * 60), 13, "#9aa1af", anchor="right")
        for i, v in enumerate(vals):
            h = v / 240 * ch
            x = x0 + i * cw2 + cw2 * 0.22
            box(c, x, y0 - h, cw2 * 0.56, h, "#4b7bec" if i != 4 else "#2851c8", 4)
            text(c, x + cw2 * 0.28, y0 + 22, days[i], 13, "#6b7385", anchor="center")
    else:
        text(c, 256, 246, "Recent orders", 18, "#1e2533", True)
        rows = [("#10482", "Ada Byrne", "£42.00", "Paid"), ("#10481", "Tom Reyes", "£18.50", "Paid"),
                ("#10480", "Mei Lin", "£96.20", "Refunded"), ("#10479", "Sam Okafor", "£27.00", "Paid"),
                ("#10478", "Lea Novak", "£64.80", "Paid"), ("#10477", "Iris Kahn", "£12.40", "Pending")]
        for i, (a, b, m, st) in enumerate(rows):
            y = 292 + i * 44
            if i:
                c.move_to(256, y - 28)
                c.line_to(W - 56, y - 28)
                c.set_source_rgb(*rgb("#eceef2"))
                c.stroke()
            text(c, 256, y, a, 16, "#6b7385")
            text(c, 350, y, b, 16, "#1e2533")
            text(c, 640, y, m, 16, "#1e2533", anchor="right")
            col = {"Paid": "#1f9d63", "Refunded": "#c0392b", "Pending": "#c98a12"}[st]
            box(c, 700, y - 18, 100, 26, col, 13)
            text(c, 750, y, st, 14, "#ffffff", True, anchor="center")
    s.write_to_png(path)


# ---------------------------------------------------------------- 3. three logo ideas for a coffee shop
def logo_a(path):
    W = H = 360
    s, c = canvas(W, H, "#fffaf2")
    c.arc(W / 2, 150, 92, 0, 2 * math.pi)
    fill(c, "#2b3a55")
    # a bird's wing: two arcs
    c.save()
    c.translate(W / 2, 150)
    c.move_to(-56, 18)
    c.curve_to(-20, -60, 30, -50, 60, -24)
    c.curve_to(26, -20, 0, 6, -56, 18)
    fill(c, "#f2b33d")
    c.arc(34, -26, 6, 0, 2 * math.pi)
    fill(c, "#2b3a55")
    c.restore()
    text(c, W / 2, 292, "KESTREL", 40, "#2b3a55", True, anchor="center")
    text(c, W / 2, 324, "COFFEE  ROASTERS", 15, "#8a6d3b", anchor="center")
    s.write_to_png(path)


def logo_b(path):
    W = H = 360
    s, c = canvas(W, H, "#f4f1ec")
    # coffee bean
    c.save()
    c.translate(W / 2, 140)
    c.rotate(0.5)
    c.scale(0.68, 1)
    c.arc(0, 0, 84, 0, 2 * math.pi)
    c.restore()
    fill(c, "#6b3f24")
    c.save()
    c.translate(W / 2, 140)
    c.rotate(0.5)
    c.move_to(0, -80)
    c.curve_to(-26, -30, 26, 30, 0, 80)
    c.set_source_rgb(*rgb("#f4f1ec"))
    c.set_line_width(9)
    c.stroke()
    c.restore()
    text(c, W / 2, 290, "kestrel", 52, "#3a2416", False, font="Georgia", anchor="center")
    s.write_to_png(path)


def logo_c(path):
    W = H = 360
    s, c = canvas(W, H, "#ffffff")
    rrect(c, 70, 40, 220, 220, 44)
    fill(c, "#d9472b")
    text(c, W / 2, 218, "K", 130, "#ffffff", True, anchor="center")
    # steam
    for dx in (-26, 0, 26):
        c.move_to(W / 2 + dx, 104)
        c.curve_to(W / 2 + dx - 10, 92, W / 2 + dx + 10, 82, W / 2 + dx, 70)
        c.set_source_rgba(1, 1, 1, 0.75)
        c.set_line_width(5)
        c.set_line_cap(cairo.LINE_CAP_ROUND)
        c.stroke()
    text(c, W / 2, 312, "Kestrel Coffee", 30, "#1f1f1f", True, anchor="center")
    s.write_to_png(path)


# ---------------------------------------------------------------- 4. usage over the last 7 days, one small chart a subscription
def usage_bars(path, vals, col, cap=100):
    W, H = 300, 84
    s, c = canvas(W, H, None)
    n = len(vals)
    bw = W / n
    for i, v in enumerate(vals):
        h = max(2, v / cap * (H - 22))
        x = i * bw + bw * 0.18
        box(c, x, H - 18 - h, bw * 0.64, h, col if i == n - 1 else col, 3)
        if i < n - 1:
            c.rectangle(x, H - 18 - h, bw * 0.64, h)
            fill(c, "#ffffff", 0.35)
        text(c, x + bw * 0.32, H - 3, "MTWTFSS"[i], 12, "#8a8f98", anchor="center")
    s.write_to_png(path)


# ---------------------------------------------------------------- 5. a meter: Work at 100% this week
def meter(path, frac, label_col):
    W, H = 300, 40
    s, c = canvas(W, H, None)
    box(c, 0, 10, W, 20, "#e6e8ec", 10)
    box(c, 0, 10, max(20, W * min(frac, 1)), 20, label_col, 10)
    s.write_to_png(path)


# ---------------------------------------------------------------- 4. the week's usage, one line a subscription (full width)
def week_chart(path):
    W, H = 1100, 300
    s, c = canvas(W, H, "#ffffff")
    rrect(c, 0.5, 0.5, W - 1, H - 1, 12)
    c.set_source_rgb(*rgb("#e3e6ea"))
    c.set_line_width(1)
    c.stroke()
    text(c, 28, 40, "Used this week", 18, "#1e2533", True)
    series = [("Work", [62, 71, 80, 88, 94, 97, 100], "#d9472b"), ("Home", [12, 20, 18, 31, 35, 38, 41], "#4b7bec"),
              ("Team", [5, 9, 9, 14, 15, 15, 17], "#1f9d63")]
    lx = W - 28
    for name, _v, col in reversed(series):
        lx -= text(c, -1000, -1000, name, 15) + 34
        c.arc(lx, 35, 6, 0, 2 * math.pi)
        fill(c, col)
        text(c, lx + 12, 40, name, 15, "#4a5160")
    x0, x1, y0, y1 = 70, W - 40, H - 46, 70
    for k in range(5):
        y = y0 - k * (y0 - y1) / 4
        c.move_to(x0, y)
        c.line_to(x1, y)
        c.set_source_rgb(*rgb("#eceef2"))
        c.set_line_width(1)
        c.stroke()
        text(c, x0 - 12, y + 5, f"{k * 25}%", 13, "#9aa1af", anchor="right")
    days = ["Fri", "Sat", "Sun", "Mon", "Tue", "Wed", "Thu"]
    step = (x1 - x0) / 6
    for i, d in enumerate(days):
        text(c, x0 + i * step, H - 18, d, 13, "#6b7385", anchor="center")
    for _n, vals, col in series:
        for i, v in enumerate(vals):
            x, y = x0 + i * step, y0 - v / 100 * (y0 - y1)
            (c.move_to if i == 0 else c.line_to)(x, y)
        c.set_source_rgb(*rgb(col))
        c.set_line_width(3)
        c.stroke()
        for i, v in enumerate(vals):
            c.arc(x0 + i * step, y0 - v / 100 * (y0 - y1), 4.5, 0, 2 * math.pi)
            fill(c, col)
    s.write_to_png(path)


def make(OUT):  # noqa: N803
    """Every picture, written in OUT (made if it isn't there): OUT."""
    os.makedirs(OUT, exist_ok=True)
    week_chart(f"{OUT}/week.png")
    tea_home(f"{OUT}/tea-before.png", False)
    tea_home(f"{OUT}/tea-after.png", True)
    dashboard(f"{OUT}/dash-before.png", False)
    dashboard(f"{OUT}/dash-after.png", True)
    logo_a(f"{OUT}/logo-a.png")
    logo_b(f"{OUT}/logo-b.png")
    logo_c(f"{OUT}/logo-c.png")
    usage_bars(f"{OUT}/use-work.png", [62, 71, 80, 88, 94, 97, 100], "#d9472b")
    usage_bars(f"{OUT}/use-home.png", [12, 20, 18, 31, 35, 38, 41], "#4b7bec")
    usage_bars(f"{OUT}/use-team.png", [5, 9, 9, 14, 15, 15, 17], "#1f9d63")
    meter(f"{OUT}/meter-work.png", 1.0, "#d9472b")
    meter(f"{OUT}/meter-home.png", 0.41, "#4b7bec")
    meter(f"{OUT}/meter-team.png", 0.17, "#1f9d63")
    return OUT
