"""emissioncontrol — what a Bot's browser tools ask of a window (Control): the script its pages are read and
driven with (PAGE_JS, built into the Android app too: android/app/build.gradle.kts reads it here), a REPL's run
(REPL_RUN, repl_bodies), and someone watching a profile's browser (Watcher), its frames scaled and made JPEGs.
"""
import base64
import hashlib
import json
import sys
import time

import gi  # noqa: E402
from gi.repository import GLib  # noqa: E402
from baudelib import wire  # noqa: E402  (its screenshots' sizes and qualities, as the phone's: the wire's schema)
from emissioncore import SCREEN, address, once  # noqa: E402


PAGE_JS = r"""
if (!globalThis.__em) globalThis.__em = (() => {
  const ids = new WeakMap(), refs = new Map();
  let n = 0;
  const ref = el => {
    let id = ids.get(el);
    if (!id) { id = 'ref_' + (++n); ids.set(el, id); refs.set(id, new WeakRef(el)); }
    return id;
  };
  const get = id => {
    const r = refs.get(id), el = r && r.deref();
    if (!el || !el.isConnected) throw new Error('no element ' + id + ' on the page now: read_page or find again');
    return el;
  };
  const visible = el => {
    const st = getComputedStyle(el);
    if (st.display === 'none' || st.visibility === 'hidden') return false;
    const b = el.getBoundingClientRect();
    return b.width > 0 && b.height > 0;
  };
  const ROLES = {A: 'link', BUTTON: 'button', SELECT: 'combobox', TEXTAREA: 'textbox', IMG: 'img', NAV: 'navigation',
    MAIN: 'main', FORM: 'form', TABLE: 'table', LI: 'listitem', UL: 'list', OL: 'list', DIALOG: 'dialog',
    OPTION: 'option', SUMMARY: 'button', IFRAME: 'iframe', VIDEO: 'video', HEADER: 'banner', FOOTER: 'contentinfo',
    H1: 'heading', H2: 'heading', H3: 'heading', H4: 'heading', H5: 'heading', H6: 'heading'};
  const ACTS = ['link', 'button', 'textbox', 'searchbox', 'checkbox', 'radio', 'combobox', 'slider', 'option',
    'menuitem', 'tab', 'switch', 'spinbutton'];
  const role = el => {
    const r = el.getAttribute('role');
    if (r) return r;
    if (el.tagName === 'INPUT') {
      const t = (el.type || 'text').toLowerCase();
      if (t === 'hidden') return null;
      return {checkbox: 'checkbox', radio: 'radio', button: 'button', submit: 'button', reset: 'button',
        image: 'button', range: 'slider', search: 'searchbox', number: 'spinbutton', file: 'button'}[t] || 'textbox';
    }
    if (el.tagName === 'A' && !el.hasAttribute('href')) return null;
    return ROLES[el.tagName] || (el.isContentEditable && el.getAttribute('contenteditable') !== null ? 'textbox' : null);
  };
  const NAMED_BY_TEXT = ['link', 'button', 'heading', 'option', 'menuitem', 'tab', 'listitem', 'cell', 'label',
    'switch', 'checkbox', 'radio', 'treeitem', 'row', 'columnheader', 'rowheader', 'tooltip', 'status', 'alert'];
  const acts = el => ACTS.includes(role(el)) || el.hasAttribute('onclick') ||
    (el.hasAttribute('tabindex') && el.tabIndex >= 0);
  const clip = (t, n = 80) => {
    t = (t || '').replace(/\s+/g, ' ').trim();
    return t.length > n ? t.slice(0, n) + '…' : t;
  };
  const name = el => {
    const l = el.getAttribute('aria-label');
    if (l) return clip(l);
    const by = el.getAttribute('aria-labelledby');
    if (by) {
      const t = by.split(/\s+/).map(i => (document.getElementById(i) || {}).innerText || '').join(' ');
      if (t.trim()) return clip(t);
    }
    if (el.labels && el.labels.length) return clip([...el.labels].map(x => x.innerText).join(' '));
    const v = el.tagName === 'INPUT' && ['submit', 'button', 'reset'].includes(el.type) ? el.value : '';
    return clip(el.getAttribute('alt') || el.getAttribute('title') || el.getAttribute('placeholder') || v ||
      (NAMED_BY_TEXT.includes(role(el)) ? el.innerText : '') || '');
  };
  const line = (el, depth) => {
    const r = role(el);
    let s = '  '.repeat(depth) + '- ' + (r || el.tagName.toLowerCase());
    const nm = name(el);
    if (nm) s += ' "' + nm.replace(/"/g, '\\"') + '"';
    s += ' [' + ref(el) + ']';
    if (el.tagName === 'A' && el.getAttribute('href')) s += ' href="' + clip(el.getAttribute('href'), 120) + '"';
    if (['textbox', 'searchbox', 'combobox', 'slider', 'spinbutton'].includes(r) && 'value' in el && el.value)
      s += ' value="' + clip(String(el.value), 80) + '"';
    if (el.type === 'checkbox' || el.type === 'radio') s += el.checked ? ' checked' : ' unchecked';
    if (el.disabled) s += ' disabled';
    if (el === document.activeElement) s += ' focused';
    return s;
  };
  const keeps = (el, all) => all ? !!role(el) : (acts(el) || role(el) === 'heading');
  const LEAVES = ['link', 'button', 'option', 'img'];
  const up = el => el.assignedSlot || el.parentElement || (el.parentNode instanceof ShadowRoot ? el.parentNode.host
    : null);
  const boxes = () => {
    // (the rects of el's ancestors that clip it, a box it's scrolled in: only those its containing blocks are in, so
    // what's fixed is clipped only from an ancestor that holds fixed things (a transform's, a filter's, …) out, and
    // what's absolute only from its positioned ancestor out; shadow roots left through their hosts; looked up once a
    // read)
    const seen = new Map();
    const look = p => {
      if (!seen.has(p)) {
        const st = getComputedStyle(p), wc = st.willChange || '';
        seen.set(p, {pos: st.position, clip: st.overflowX === 'visible' && st.overflowY === 'visible' ? null
          : p.getBoundingClientRect(), holds: st.transform !== 'none' || st.filter !== 'none' ||
          st.perspective !== 'none' || /paint|layout|strict|content/.test(st.contain || '') ||
          /transform|filter|perspective/.test(wc)});
      }
      return seen.get(p);
    };
    return function* (el) {
      let pos = getComputedStyle(el).position;
      for (let p = up(el); p && p !== document.body && p !== document.documentElement; p = up(p)) {
        const box = look(p);
        if (pos === 'fixed' && !box.holds) continue;
        if (pos === 'absolute' && box.pos === 'static' && !box.holds) continue;
        if (box.clip) yield box.clip;
        pos = box.pos;
      }
    };
  };
  const read = (root, all, max, near) => {
    // (near: only what's within that many screens of the viewport, and of any box it's scrolled in; the rest counted,
    // above and below; null: all of root)
    const out = [], H = innerHeight, clips = boxes();
    let size = 0, above = 0, below = 0;
    let cut = false;
    const put = l => { if (size + l.length + 1 > max) { cut = true; return; } out.push(l); size += l.length + 1; };
    const where = el => {
      if (near === null) return 0;
      const b = el.getBoundingClientRect();
      if (b.bottom < -near * H) return -1;
      if (b.top > (1 + near) * H) return 1;
      for (const c of clips(el)) {
        if (b.bottom < c.top - near * c.height) return -1;
        if (b.top > c.bottom + near * c.height) return 1;
      }
      return 0;
    };
    const walk = (el, depth) => {
      if (cut || !(el instanceof Element)) return;
      if (el.matches('script, style, noscript, template, head') || !visible(el)) return;
      const r = role(el), keep = keeps(el, all), at = keep || all ? where(el) : 0;
      let d = depth;
      if (keep && at === 0) { put(line(el, all ? depth : 0)); d = depth + 1; }
      else if (keep) at < 0 ? above++ : below++;
      if (all && at === 0) {
        const own = [...el.childNodes].filter(c => c.nodeType === 3).map(c => c.textContent).join(' ');
        if (own.trim() && !(keep && name(el))) put('  '.repeat(d) + '- text "' + clip(own, 300) + '"');
      }
      if (keep && LEAVES.includes(r)) return;
      for (const c of el.children) walk(c, d);
      if (el.shadowRoot) for (const c of el.shadowRoot.children) walk(c, d);
    };
    walk(root, 0);
    return {text: out.join('\n'), above, below, cut};
  };
  const onscreen = () => {
    // (read_page's elements, links, buttons, fields and headings, in the viewport now, and not clipped out of it by a
    // box they're scrolled in: in the page's order)
    const out = [], W = innerWidth, H = innerHeight, clips = boxes();
    const shown = el => {
      const b = el.getBoundingClientRect();
      let l = Math.max(b.left, 0), t = Math.max(b.top, 0), r = Math.min(b.right, W), bt = Math.min(b.bottom, H);
      for (const c of clips(el)) {
        if (r <= l || bt <= t) break;
        l = Math.max(l, c.left); t = Math.max(t, c.top); r = Math.min(r, c.right); bt = Math.min(bt, c.bottom);
      }
      return r > l && bt > t;
    };
    const walk = el => {
      if (!(el instanceof Element) || el.matches('script, style, noscript, template, head') || !visible(el)) return;
      if (keeps(el, false)) {
        if (shown(el)) out.push(el);
        if (LEAVES.includes(role(el))) return;
      }
      for (const c of el.children) walk(c);
      if (el.shadowRoot) for (const c of el.shadowRoot.children) walk(c);
    };
    walk(document.body);
    return out;
  };
  const came = (before, most, max) => {
    // (what's in the viewport now that wasn't in before, a Set of elements: at most most lines, max characters)
    const now = onscreen().filter(el => !before.has(el)), out = [];
    let size = 0;
    for (const el of now) {
      const l = line(el, 0);
      if (out.length >= most || size + l.length > max) break;
      out.push(l);
      size += l.length + 1;
    }
    return {lines: out, more: now.length - out.length};
  };
  const find = (q, limit) => {
    q = q.toLowerCase();
    const out = [];
    for (const el of document.querySelectorAll('*')) {
      if (out.length >= limit) break;
      const r = role(el);
      if (!(r || acts(el)) || !visible(el)) continue;
      const hay = [r, name(el), el.getAttribute('placeholder'), el.id, el.getAttribute('name'),
        el.tagName === 'A' ? el.getAttribute('href') : ''].join(' ').toLowerCase();
      if (hay.includes(q)) out.push(line(el, 0));
    }
    return out;
  };
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const centre = async el => {
    el.scrollIntoView({block: 'center', inline: 'center', behavior: 'instant'});
    await sleep(60);
    const b = el.getBoundingClientRect(), x = b.left + b.width / 2, y = b.top + b.height / 2;
    const hit = document.elementFromPoint(x, y);
    return {x, y, covered: hit && hit !== el && !el.contains(hit) && !hit.contains(el) ? line(hit, 0) : null};
  };
  const editable = el => el && (el.isContentEditable ||
    (el.tagName === 'TEXTAREA') || (el.tagName === 'INPUT' && !['checkbox', 'radio', 'button', 'submit', 'reset',
      'file', 'image', 'range', 'color', 'hidden'].includes((el.type || 'text').toLowerCase())));
  const setValue = (el, v) => {
    const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : el.tagName === 'SELECT'
      ? HTMLSelectElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, v);
    el.dispatchEvent(new Event('input', {bubbles: true}));
    el.dispatchEvent(new Event('change', {bubbles: true}));
  };
  return {ref, get, read, onscreen, came, find, centre, line, editable, setValue, sleep, visible};
})();
const em = globalThis.__em;
"""


def guarded_by(answer):
    return lambda fn: guarded(fn, answer)


def guarded(fn, answer):
    """fn, its errors answered (a callback's, which nothing else would catch: the request would wait for good)."""
    def run(*a):
        try:
            return fn(*a)
        except Exception as e:
            sys.excepthook(*sys.exc_info())
            answer({"error": f"{type(e).__name__}: {e}"})
            return None
    return run


REPL_THREW = "\u2063threw"  # (what a REPL's run answers when its text throws: its error)
REPL_RUN = """let __v;
try { __v = await (async () => {
BODY
})(); } catch (e) { return {"\\u2063threw": String(e) + (e && e.stack ? '\\n' + e.stack : '')}; }
try { JSON.stringify(__v); return __v === undefined ? null : __v; } catch (e) { return String(__v); }"""
REPL_SPLITS = 6  # (the last statements tried as the one whose value comes back)


def repl_bodies(text):
    """text, as a REPL reads it, as async function bodies to try in turn: all of it one expression (returned); then,
    from the last statement back, the statements before one and that one returned; then all of it, returning nothing.
    Statements are split at a ; or a line's end outside brackets, strings, templates and comments (one that a line
    goes on past, an operator before or after it, isn't a split)."""
    text = text.strip().rstrip(";").rstrip()
    splits, depth, i, n = [], 0, 0, len(text)
    while i < n:
        c = text[i]
        if c in "'\"`":
            j = i + 1
            while j < n and text[j] != c:
                j += 2 if text[j] == "\\" else 1
            i = j + 1
            continue
        if text.startswith("//", i):
            i = text.find("\n", i)
            i = n if i < 0 else i
            continue
        if text.startswith("/*", i):
            i = text.find("*/", i + 2)
            i = n if i < 0 else i + 2
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth = max(0, depth - 1)
            if c == "}" and depth == 0:
                splits.append(i + 1)
        elif depth == 0 and c == ";":
            splits.append(i + 1)
        elif depth == 0 and c == "\n":
            before, after = text[:i].rstrip()[-1:], text[i:].lstrip()[:1]
            if before and after and before not in "+-*/%&|^.,?:=<>([{!~" and after not in "+-*/%&|^.,?:=<>)]}([`":
                splits.append(i + 1)
        i += 1
    bodies, lasts = [f"return ({text}\n);"], set()
    for at in sorted(set(splits), reverse=True):
        head, last = text[:at], text[at:].strip()
        if last and last not in lasts and len(lasts) < REPL_SPLITS:
            lasts.add(last)
            bodies.append(f"{head}\nreturn ({last}\n);")
    bodies.append(text)
    return bodies


class Control:
    """What a Bot's browser tools ask of a window (emissiontools, through the host process and the socket): a request,
    answered with a dict. Input's real (the pointer moved to a place, then pressed; keys one at a time), as computer
    use's is; a page's text comes from the page itself, and what's on it is named by refs read_page and find give."""
    KEY_GAP_MS = 30  # (between keys, as a person's: kept steady)
    TYPE_GAP_MS = 12  # (between characters typed: xdotool type's own pace)
    LOAD_S = 30  # (a page's load waited for this long at most)
    JS_S = 120  # (a script's end waited for this long at most: past what a Bot's tools wait for an answer)
    SHOT_MAX = wire.CONSTANTS["SHOT_MAX"]  # (a screenshot's longest side: about XGA's, as computer use has it; zoom for
    # more)
    ZOOM_MAX = wire.CONSTANTS["ZOOM_MAX"]  # (a zoom's longest side: what a model sees whole)
    NEAR = 1  # (read_page: what's shown, and this many screens above and below it)
    CAME_LINES, CAME_CHARS = 30, 1500  # (a scroll's answer: at most this much of what came into view)

    def __init__(self, win):
        self.win = win
        self.scales = {}  # (each tab's last screenshot's pixels to the page's: by tab id)
        self.reads = {}  # (each tab's last read_page: what was asked and a hash of what it gave, by tab id)
        self.queue = []  # (input waiting its turn: (op, msg, answer), the first being done)
        self.doing = None  # (the one of them begun: next_input)

    # (tabs)

    def tab(self, msg):
        want = msg.get("tab")
        for page, t in self.win.tabs.items():
            if want in (None, "", t.id) and (want or page is self.win.view.get_selected_page()):
                self.win.view.set_selected_page(page)
                return t
        if want:
            raise ValueError(f"no tab {want}: tabs_context lists them")
        return self.win.open("about:blank")

    def describe(self, t):
        st = t.web.state()
        return {"tab": t.id, "title": st.get("title") or "", "url": st.get("url") or "",
                "loading": bool(st.get("loading")), "selected": t is self.win.current(),
                **({"prompts": got} if (got := self.win.prompts(t)) else {})}

    def settle(self, t, answer, extra=None, started=None):
        """answer(describe(t) + extra) once t's page has loaded (or LOAD_S has gone), after a moment to start."""
        started = started or GLib.get_monotonic_time()

        @guarded_by(answer)
        def look():
            st = t.web.state() if t.web.view is not None else {}
            waited = (GLib.get_monotonic_time() - started) / 1e6
            if waited < 0.35 or (st.get("loading") and waited < self.LOAD_S and not self.win.prompts(t)):
                return True
            answer({**self.describe(t), **(extra or {})})
            return False
        GLib.timeout_add(100, look)

    def js(self, t, body, answer, then=None, page=False, failed=None):
        """body run in t's page (Emission's world, PAGE_JS's em there; page: the page's own world, for javascript):
        then(result) given what it returned, or the error answered (or given to failed, if there's one)."""
        if t.web.view is None or t not in self.win.tabs.values():
            answer({"error": f"tab {t.id} has closed: tabs_context lists the open ones"})
            return
        if (waiting := [p for p in self.win.prompts(t) if p["kind"] in ("alert", "confirm", "prompt")]):
            answer({"error": "the page is waiting on its prompt, and runs nothing till it's answered (answer_prompt)",
                    "prompts": waiting})
            return

        def done(text, why):
            if why is not None:
                (failed or (lambda w: answer({"error": w})))(why)
                return
            try:
                value = json.loads(text)
            except ValueError:
                value = text
            (then or (lambda v: answer({"result": v})))(value)
        finish = once(guarded(done, answer))
        # (a script that never settles, a promise nothing resolves: said so once whoever asked has stopped waiting,
        # so its request and connection go, and input queued behind it gets its turn)
        timer = [GLib.timeout_add_seconds(self.JS_S, lambda: (
            timer.clear(), finish(None, f"the script hadn't finished after {self.JS_S} s"))[1] and False)]

        def ended(text, why):
            if timer:
                GLib.source_remove(timer.pop())
            finish(text, why)
        t.web.run_js(body if page else PAGE_JS + body, ended, page=page)

    INPUT = {"click", "drag", "hover", "type", "key", "scroll", "form_input", "navigate", "new_tab", "close_tab",
             "select_tab", "answer_prompt", "resize", "copy", "paste"}  # (what changes the page: done in the order it came)

    def run(self, op, msg, answer):
        fn = getattr(self, "op_" + str(op), None)
        if fn is None:
            raise ValueError(f"no such request: {op}")
        if op not in self.INPUT:
            return fn(msg, answer)
        self.queue.append((fn, msg, answer))
        if len(self.queue) == 1:
            self.next_input()

    def next_input(self):
        """The first of what's waiting done (the next once it's answered, whatever the answer), once (doing): this
        is asked again while it's being done, when the request after the last one answered comes before the idle
        moment that answer left (run begins it, then that moment's here)."""
        while self.queue and self.queue[0] is not self.doing:
            self.doing = self.queue[0]
            fn, msg, answer = self.queue[0]

            def done(reply, answer=answer):
                answer(reply)
                if self.queue and self.queue[0][2] is answer:
                    self.queue.pop(0)
                    GLib.idle_add(lambda: self.next_input() and False)
            try:
                fn(msg, once(done))
                return
            except Exception as e:  # (said, and the next done)
                answer({"error": str(e) if isinstance(e, (ValueError, OSError)) else f"{type(e).__name__}: {e}"})
                self.queue.pop(0)

    def op_tabs(self, msg, answer):
        answer({"profile": self.win.profile, "tabs": [self.describe(t) for t in self.win.tabs.values()],
                "downloads": self.win.downloads[-10:]})

    def op_select_tab(self, msg, answer):
        answer(self.describe(self.tab(msg)))

    def op_new_tab(self, msg, answer):
        t = self.win.open(address(msg.get("url") or "about:blank"))
        self.settle(t, answer)

    def op_close_tab(self, msg, answer):
        t = self.tab(msg)
        if len(self.win.tabs) == 1:
            self.win.open("about:blank")
        self.win.close_tab(t)
        self.reads.pop(t.id, None)
        self.scales.pop(t.id, None)
        answer({"closed": t.id, "tabs": [self.describe(x) for x in self.win.tabs.values() if x is not t]})

    def op_navigate(self, msg, answer):
        t, url = self.tab(msg), msg.get("url") or ""
        {"back": t.web.back, "forward": t.web.forward, "reload": t.web.reload}.get(
            url, lambda: t.web.load(address(url)))()
        self.settle(t, answer)

    # (looking)

    def picture(self, t, answer, then):
        """then(pixbuf, view) for t's page as it's shown (view: its size, CSS pixels; the pixbuf may have more)."""
        def sized(view):
            def shot(png, why):
                if png is None:
                    answer({"error": f"no screenshot: {why}"})
                    return
                gi.require_version("GdkPixbuf", "2.0")
                from gi.repository import GdkPixbuf
                loader = GdkPixbuf.PixbufLoader.new_with_type("png")
                loader.write(png)
                loader.close()
                then(loader.get_pixbuf(), {"w": max(1, int(view["w"])), "h": max(1, int(view["h"]))})
            GLib.timeout_add(120, guarded(lambda: t.web.snapshot(guarded(shot, answer)) and False, answer))
        self.js(t, "return {w: innerWidth, h: innerHeight}", answer, sized)

    @staticmethod
    def jpeg_of(pic, x, y, w, h, pw, ph, quality):
        """pic's part (x, y, w, h: its pixels) as a pw by ph JPEG, on white (JPEG has no alpha)."""
        from gi.repository import GdkPixbuf
        flat = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, pw, ph)
        flat.fill(0xffffffff)
        pic.composite(flat, 0, 0, pw, ph, -x * pw / w, -y * ph / h, pw / w, ph / h, GdkPixbuf.InterpType.BILINEAR, 255)
        return base64.b64encode(flat.save_to_bufferv("jpeg", ["quality"], [str(quality)])[1]).decode()

    def op_screenshot(self, msg, answer):
        t = self.tab(msg)

        def shot(pic, view):
            w, h = view["w"], view["h"]
            scale = min(1.0, self.SHOT_MAX / max(w, h))
            pw, ph = max(1, round(w * scale)), max(1, round(h * scale))
            image = self.jpeg_of(pic, 0, 0, pic.get_width(), pic.get_height(), pw, ph,
                                 wire.CONSTANTS["SHOT_QUALITY"])
            self.scales[t.id] = scale
            answer({**self.describe(t), "image": image, "mime": "image/jpeg", "width": pw, "height": ph,
                    "note": "x and y for click, scroll and zoom are this picture's pixels"})
        self.picture(t, answer, shot)

    def op_zoom(self, msg, answer):
        """A region of the last screenshot ([x0, y0, x1, y1], its pixels), at the page's own detail: what x and y
        are in stays the screenshot's."""
        t = self.tab(msg)
        region = msg.get("region")
        if not (isinstance(region, list) and len(region) == 4 and all(isinstance(v, (int, float)) for v in region)
                and region[2] > region[0] and region[3] > region[1]):
            raise ValueError("zoom needs a region: [x0, y0, x1, y1], the screenshot's pixels, x1 past x0, y1 past y0")

        if t.id not in self.scales:
            raise ValueError("zoom is of a screenshot's region: take one first")

        def shot(pic, view):
            w, h = view["w"], view["h"]
            scale = self.scales[t.id]
            k = pic.get_width() / w  # (the picture's pixels to the page's CSS ones: 2 on a Retina display)
            x0, y0 = max(0.0, region[0] / scale), max(0.0, region[1] / scale)
            x1, y1 = min(float(w), region[2] / scale), min(float(h), region[3] / scale)
            if x1 - x0 < 1 or y1 - y0 < 1:
                return answer({"error": f"that region is off the page (the screenshot is {round(w * scale)} by "
                                        f"{round(h * scale)})"})
            px, py, pw, ph = x0 * k, y0 * k, (x1 - x0) * k, (y1 - y0) * k
            fit = min(1.0, self.ZOOM_MAX / max(pw, ph))
            ow, oh = max(1, round(pw * fit)), max(1, round(ph * fit))
            image = self.jpeg_of(pic, px, py, pw, ph, ow, oh, wire.CONSTANTS["ZOOM_QUALITY"])
            answer({**self.describe(t), "image": image, "mime": "image/jpeg",
                    "width": ow, "height": oh, "region": [round(v) for v in region],
                    "note": "x and y for click and scroll are still the screenshot's pixels"})
        self.picture(t, answer, shot)

    def op_read_page(self, msg, answer):
        """The page's elements near what's shown (all of ref's part, if a ref's given); or, if nothing's changed
        since this tab's last read (its address, and what it gave), a line saying so: once, the next read giving
        them again."""
        t = self.tab(msg)
        root = f"em.get({json.dumps(msg['ref'])})" if msg.get("ref") else "document.body"
        near = None if msg.get("ref") else self.NEAR

        def read(got):
            text = got["text"]
            if got.get("cut"):
                text += "\n(… cut short: read a part with ref_id, or look with find)"
            off = [f"{n} {side}" for n, side in ((got.get("above"), "above"), (got.get("below"), "below")) if n]
            if off:
                text += (f"\n(Not listed: {' and '.join(off).replace(' ', ' elements ', 1)}, more than a screen "
                         "from what's shown. Scroll, or find, reaches them.)")
            asked = (t.web.state().get("url") or "", msg.get("filter") or "", msg.get("ref") or "",
                     msg.get("max_chars") or 0)
            seen = (asked, hashlib.sha1(text.encode()).hexdigest())
            if self.reads.get(t.id) == seen:
                del self.reads[t.id]
                return answer({**self.describe(t), "page": "Unchanged since your last read_page of this tab. (Read "
                                                           "again if you no longer have it.)"})
            self.reads[t.id] = seen
            answer({**self.describe(t), "page": text})
        self.js(t, f"return em.read({root}, {json.dumps(msg.get('filter') == 'all')}, "
                   f"{int(msg.get('max_chars') or 30000)}, {json.dumps(near)})", answer, read)

    def op_find(self, msg, answer):
        t = self.tab(msg)
        if not str(msg.get("query") or "").strip():
            raise ValueError("find needs a query")
        self.js(t, f"return em.find({json.dumps(str(msg['query']))}, {int(msg.get('limit') or 20)})",
                answer, lambda found: answer({**self.describe(t), "found": found}))

    def op_get_text(self, msg, answer):
        t = self.tab(msg)
        cap = int(msg.get("max_chars") or 50000)
        self.js(t, f"const el = document.querySelector('main, article, [role=main]') || document.body;"
                   f"const t = (el.innerText || ''); return t.length > {cap} ? t.slice(0, {cap}) + "
                   f"'\\n(… ' + (t.length - {cap}) + ' more characters)' : t", answer,
                lambda text: answer({**self.describe(t), "text": text}))

    def op_javascript(self, msg, answer):
        """text run in the page's own world as a REPL runs it (its last expression's value back, await anywhere): the
        first of repl_bodies' readings of it that compiles, run (compiled in Emission's world first, where the page's
        rules on scripts don't reach, without running: a syntax error's left out of the page's console); what it
        throws said as the error. code: an async function's body, run as it is."""
        t = self.tab(msg)
        if "text" not in msg:
            return self.js(t, str(msg.get("code") or ""), answer,
                           lambda value: answer({**self.describe(t), "result": value}), page=True)
        bodies = repl_bodies(str(msg.get("text") or ""))

        def ran(value):
            if isinstance(value, dict) and set(value) == {REPL_THREW}:
                return answer({"error": value[REPL_THREW]})
            answer({**self.describe(t), "result": value})

        def run(i):
            self.js(t, REPL_RUN.replace("BODY", bodies[i]), answer, ran, page=True)

        def tried(i):  # (where it couldn't be compiled there, each reading run in turn until one isn't a SyntaxError)
            self.js(t, REPL_RUN.replace("BODY", bodies[i]), answer, ran, page=True, failed=lambda why: (
                tried(i + 1) if "SyntaxError" in str(why) and i + 1 < len(bodies) else answer({"error": why})))

        def chose(got):
            if got.get("at") is not None:
                return run(got["at"])
            if got.get("unchecked"):
                return tried(0)
            answer({"error": got.get("why") or "SyntaxError"})
        self.js(t, f"const bodies = {json.dumps(bodies)}, AF = (async () => {{}}).constructor; let why = null;"
                   "for (let i = 0; i < bodies.length; i++) {"
                   "  try { new AF(bodies[i]); return {at: i}; } catch (e) {"
                   "    if (!(e instanceof SyntaxError)) return {unchecked: true}; why = String(e); } }"
                   "return {why}", answer, chose)

    # (doing)

    def point(self, t, msg, answer, then):
        """then(x, y, covered) for a ref's centre (scrolled to first), or for x and y as the last screenshot had them."""
        if msg.get("ref"):
            self.js(t, f"return await em.centre(em.get({json.dumps(msg['ref'])}))", answer,
                    lambda c: then(c["x"], c["y"], c.get("covered")))
        elif msg.get("x") is not None and msg.get("y") is not None:
            scale = 1.0 if msg.get("css") else self.scales.get(t.id, 1.0)  # (css: the page's own pixels)
            then(float(msg["x"]) / scale, float(msg["y"]) / scale, None)
        else:
            raise ValueError("a ref, or x and y, says where")

    def pointed(self, msg, t, kind, x, y, **more):
        """What the Bot did with the mouse (not the Browser page's own), for its pointer: drawn here if it's hosted,
        else told its watchers (BotPointer)."""
        if not msg.get("from_pane"):
            self.win.pointer.add(kind, x, y, tab=t.id, played=self.win.hosted, **more)

    def op_click(self, msg, answer):
        t = self.tab(msg)
        mods = [m for m in (msg.get("modifiers") or []) if m in ("shift", "ctrl", "alt", "cmd")]

        def at(x, y, covered):
            started = GLib.get_monotonic_time()
            self.pointed(msg, t, "click", x, y, count=1 if msg.get("press") else max(1, int(msg.get("count") or 1)))
            t.web.click(x, y, msg.get("button") if msg.get("button") in ("right", "middle") else "left",
                        max(1, int(msg.get("count") or 1)), mods, press=int(msg["press"]) if msg.get("press") else None)
            if not t.web.keyed():  # (a window that isn't key: what's clicked given focus, as a click would give it)
                t.web.run_js(PAGE_JS + f"const el = document.elementFromPoint({x}, {y});"
                             "const f = el && el.closest('input, textarea, select, [contenteditable], button, a[href],"
                             " [tabindex]'); if (f && f.focus) f.focus(); return true", lambda *_: None)
            if msg.get("quick"):  # (the Browser page's own click: answered done, its page seen in the frames)
                return answer({"clicked": [round(x), round(y)]})
            k = 1.0 if msg.get("css") else self.scales.get(t.id, 1.0)  # (said in the screenshot's pixels, as asked)
            self.settle(t, answer, {"clicked": [round(x * k), round(y * k)],
                                    **({"covered_by": covered} if covered else {})}, started)
        self.point(t, msg, answer, at)

    def op_drag(self, msg, answer):
        """A drag along path ([[x, y], ...], the page's CSS pixels if css, else the last screenshot's)."""
        t = self.tab(msg)
        scale = 1.0 if msg.get("css") else self.scales.get(t.id, 1.0)
        path = [(float(p[0]) / scale, float(p[1]) / scale) for p in msg.get("path") or [] if len(p) == 2]
        if len(path) < 2:
            raise ValueError("a drag needs a path of two points at least")
        started = GLib.get_monotonic_time()
        self.pointed(msg, t, "drag", *path[0], path=path)
        t.web.drag(path)
        if msg.get("quick"):
            return answer({"dragged": len(path)})
        self.settle(t, answer, {"dragged": len(path)}, started)

    def op_hover(self, msg, answer):
        t = self.tab(msg)
        if msg.get("quick"):
            return self.point(t, msg, answer, lambda x, y, _c: (t.web.move(x, y), answer({"hovered": True})))
        self.point(t, msg, answer, lambda x, y, _c: (self.pointed(msg, t, "hover", x, y), t.web.move(x, y),
                                                     GLib.timeout_add(80, lambda: answer(self.describe(t)) and False)))

    def op_type(self, msg, answer):
        """text typed into the focused field (or ref's, focused first), a key press a character at a time, as a
        keyboard would type it (a character no key here makes: put in as the page's own editing would)."""
        t, text = self.tab(msg), str(msg.get("text") or "")
        focus = f"em.get({json.dumps(msg['ref'])}).focus();" if msg.get("ref") else ""
        left = list(text)

        def read_back():
            self.js(t, "const el = em.typed;"
                       "if (!el || !el.isConnected) return null;"
                       "const v = 'value' in el ? String(el.value) : el.textContent;"
                       "return v.length > 2000 ? v.slice(0, 2000) + '…' : v", answer,
                    lambda value: answer({**self.describe(t), "value": value} if value is not None else
                                         {**self.describe(t), "note": "the field's gone (the page changed)"}))

        @guarded_by(answer)
        def next_char():
            if not left:
                read_back()
                return False
            ch = left.pop(0)
            if not t.web.type_char(ch):
                self.js(t, f"const ch = {json.dumps(ch)}, el = em.typed;"
                           "if (!document.execCommand('insertText', false, ch) && el) {"
                           "  if ('value' in el) em.setValue(el, el.value + ch); else el.textContent += ch; }"
                           "return true", answer,
                        lambda _v: GLib.timeout_add(self.TYPE_GAP_MS, next_char))
                return False
            return True

        def ready(_v):
            if next_char():
                GLib.timeout_add(self.TYPE_GAP_MS, next_char)
        anywhere = json.dumps(bool(msg.get("anywhere")))
        self.js(t, focus + "const el = document.activeElement;"
                f"if (!em.editable(el) && !{anywhere}) throw new Error('nothing to type into is focused: click the "
                "field, or give its ref'); em.typed = em.editable(el) ? el : null; return true", answer, ready)

    def op_copy(self, msg, answer):
        """The text for a watcher's own clipboard (its ⌘C or Ctrl+C): what the page's own copy (cut: cut) handler
        puts in a DataTransfer, else the text selected in the page (in the focused field, its selection there; none
        from a password's); cut: taken out of the page too, as cutting does, unless its handler took the event.
        handled: the page's handler took the event itself."""
        t, cut = self.tab(msg), bool(msg.get("cut"))
        self.js(t, "const el = document.activeElement;"
                   "if (el && el.type === 'password') return {text: '', handled: false};"
                   "const data = new DataTransfer();"
                   f"const ev = new ClipboardEvent({json.dumps('cut' if cut else 'copy')}, "
                   "{clipboardData: data, bubbles: true, cancelable: true});"
                   "const handled = !(el || document.body).dispatchEvent(ev);"
                   "let text = data.getData('text/plain');"
                   "if (!text && !handled) {"
                   "  if (el && 'value' in el && typeof el.selectionStart === 'number')"
                   "    text = el.value.slice(el.selectionStart, el.selectionEnd);"
                   "  else text = String(getSelection());"
                   f"  if (text && {json.dumps(cut)}) document.execCommand('delete');"
                   "}"
                   "return {text: text || '', handled}",
                answer, lambda r: answer({**self.describe(t), "text": r["text"], "handled": r["handled"]}))

    def op_paste(self, msg, answer):
        """text, from a watcher's own clipboard (its ⌘V or Ctrl+V), pasted where the page has its caret: offered the
        page as a paste (its own handler can take it), else put in as its editing would type it."""
        t, text = self.tab(msg), str(msg.get("text") or "")
        self.js(t, f"const text = {json.dumps(text)}, el = document.activeElement;"
                   "const data = new DataTransfer(); data.setData('text/plain', text);"
                   "const ev = new ClipboardEvent('paste', {clipboardData: data, bubbles: true, cancelable: true});"
                   "if (!(el || document.body).dispatchEvent(ev)) return true;"
                   "if (!em.editable(el)) return false;"
                   "if (!document.execCommand('insertText', false, text) && 'value' in el)"
                   "  em.setValue(el, el.value.slice(0, el.selectionStart) + text + el.value.slice(el.selectionEnd));"
                   "return true", answer, lambda ok: answer({**self.describe(t), "pasted": bool(ok)}))

    def op_key(self, msg, answer):
        t = self.tab(msg)
        combos = str(msg.get("keys") or "").split()
        if not combos:
            raise ValueError("key needs keys: Enter, Tab, cmd+a, Escape, …")
        names = {"return": "Enter", "enter": "Enter", "esc": "Escape", "escape": "Escape", "tab": "Tab",
                 "backspace": "Backspace", "delete": "Delete", "del": "Delete", "space": "Space", "up": "Up",
                 "down": "Down", "left": "Left", "right": "Right", "home": "Home", "end": "End",
                 "pageup": "PageUp", "pagedown": "PageDown"}
        mod_names = {"shift": "shift", "ctrl": "ctrl", "control": "ctrl", "alt": "alt", "option": "alt",
                     "cmd": "cmd", "command": "cmd", "meta": "cmd", "super": "cmd"}
        presses = []
        for combo in combos:
            parts = combo.split("+")
            mods = tuple(sorted({mod_names[p.lower()] for p in parts[:-1] if p.lower() in mod_names}))
            k = parts[-1]
            k = names.get(k.lower(), k)
            if len(k) == 1 and k.isupper():
                k, mods = k.lower(), tuple(sorted(set(mods) | {"shift"}))
            presses.append((k, mods))
        started = GLib.get_monotonic_time()

        def press():
            if not presses:
                self.settle(t, answer, None, started)
                return False
            k, mods = presses.pop(0)
            try:
                t.web.key(k, mods)
            except Exception as e:
                answer({"error": f"{type(e).__name__}: {e}" if not isinstance(e, (ValueError, OSError)) else str(e)})
                return False
            return True
        if press():
            GLib.timeout_add(self.KEY_GAP_MS, press)

    def op_scroll(self, msg, answer):
        """A real mouse wheel turned over a ref, a point of the last screenshot, or the page's middle: dy pixels
        down (600 if none's given; up negative), dx right; what's under the pointer scrolls."""
        t = self.tab(msg)
        dx, dy = int(msg.get("dx") or 0), int(msg.get("dy") if msg.get("dy") is not None else 600)
        under = ("let el = document.elementFromPoint({x}, {y});"
                 "while (el && el !== document.body && el !== document.documentElement && !("
                 "el.scrollHeight > el.clientHeight + 2 && /(auto|scroll)/.test(getComputedStyle(el).overflowY)) && !("
                 "el.scrollWidth > el.clientWidth + 2 && /(auto|scroll)/.test(getComputedStyle(el).overflowX)))"
                 " el = el.parentElement;"
                 "const inner = !!el && el !== document.body && el !== document.documentElement;"
                 "const s = inner ? el : document.scrollingElement;")
        if msg.get("quick") and msg.get("x") is not None:  # (the Browser page's own wheel: turned, nothing waited for;
            def turned(x, y, _c):                               # with no display of its own, by script, as below)
                try:
                    t.web.wheel(x, y, dx, dy, quick=True)
                except OSError:
                    t.web.run_js(under.format(x=x, y=y) + f"s.scrollBy({{left: {dx}, top: {dy}, behavior: 'instant'}});"
                                 "return true", lambda *_: None, page=True)
                answer({"scrolled": [dx, dy]})
            return self.point(t, msg, answer, turned)

        def at(x, y, _c):
            self.pointed(msg, t, "scroll", x, y, dx=dx, dy=dy)

            def chosen(inner):
                how = "wheel"
                try:
                    t.web.wheel(x, y, dx, dy)
                    extra = ""
                except OSError:
                    how = "script"
                    extra = f"s.scrollBy({{left: {dx}, top: {dy}, behavior: 'instant'}});"
                GLib.timeout_add(250, guarded(lambda: self.js(
                    t, under.format(x=x, y=y) + extra + "await em.sleep(120);"
                    f"const came = em.came(em.seen || new Set(), {self.CAME_LINES}, {self.CAME_CHARS});"
                    "em.seen = null;"
                    "return {where: {x: s.scrollLeft, y: s.scrollTop, height: s.scrollHeight, inner}, came}", answer,
                    lambda got: answer({**self.describe(t), "scrolled": got["where"], "by": how,
                                        "in_view": in_view(got["came"])})) and False, answer))
            self.js(t, under.format(x=x, y=y) + "return inner", answer, chosen)

        def in_view(came):
            if not came["lines"]:
                return "Nothing new came into view."
            more = f"\n(… and {came['more']} more: read_page lists them)" if came["more"] else ""
            return "Came into view:\n" + "\n".join(came["lines"]) + more
        # (what's in view first: before a ref's element is scrolled to, which may bring things into view itself)
        if msg.get("ref") or msg.get("x") is not None:
            self.js(t, "em.seen = new Set(em.onscreen()); return true", answer,
                    lambda _v: self.point(t, msg, answer, at))
        else:
            self.js(t, "em.seen = new Set(em.onscreen()); return {x: innerWidth / 2, y: innerHeight / 2}", answer,
                    lambda c: at(c["x"], c["y"], None))

    def op_console(self, msg, answer):
        """What the page has said in its console (and its errors), newest last: pattern, a regular expression, only
        those matching it; onlyErrors, only errors; clear, forgotten after."""
        t = self.tab(msg)
        self.js(t, "const log = window[Symbol.for('emission.console')] || [];"
                   f"const p = {json.dumps(str(msg.get('pattern') or ''))};"
                   "let re = null;"
                   "try { re = p ? new RegExp(p) : null; } catch (e) { re = {test: x => x.includes(p)}; }"
                   f"const errors = {json.dumps(bool(msg.get('onlyErrors')))};"
                   "const got = log.filter(e => (!re || re.test(e.text)) && (!errors || e.level === 'error'))"
                   f".slice(-{max(1, int(msg.get('limit') or 100))});"
                   f"if ({json.dumps(bool(msg.get('clear')))}) log.length = 0; return got", answer,
                lambda got: answer({**self.describe(t), "console": got}), page=True)

    def op_network(self, msg, answer):
        """What the page has loaded (itself, and its resources), newest last: urlPattern, only URLs with it in them;
        clear, those forgotten after."""
        t = self.tab(msg)
        self.js(t, f"const p = {json.dumps(str(msg.get('urlPattern') or ''))}, K = Symbol.for('emission.network');"
                   "const since = window[K] ?? -1;"
                   "const got = [...performance.getEntriesByType('navigation'),"
                   " ...performance.getEntriesByType('resource')]"
                   ".filter(e => e.startTime > since && (!p || e.name.includes(p)))"
                   f".slice(-{max(1, int(msg.get('limit') or 100))}).map(e => ({{url: e.name, type: e.initiatorType || "
                   "e.entryType, status: e.responseStatus || null, ms: Math.round(e.duration), "
                   "bytes: e.transferSize || null}));"
                   f"if ({json.dumps(bool(msg.get('clear')))}) window[K] = performance.now(); return got", answer,
                lambda got: answer({**self.describe(t), "requests": got}))

    def op_resize(self, msg, answer):
        if self.win.hosted:  # (in Baude: its page laid out at this when it's not shown, its pane's size when it is)
            return self.resize_hosted(msg, answer)
        return self.resize_window(msg, answer)

    def resize_hosted(self, msg, answer):
        t = self.tab(msg)
        w, h = int(msg.get("width") or 0), int(msg.get("height") or 0)
        if not (200 <= w <= SCREEN[0] - 200 and 200 <= h <= SCREEN[1] - 300):
            raise ValueError(f"width between 200 and {SCREEN[0] - 200}, height between 200 and {SCREEN[1] - 300}")
        if msg.get("fit"):  # (a pane watching it streamed, its page out of sight: laid out at the pane's shape while
            if self.win.watchers:  # it's watched, the Bot's own size again once it isn't: unwatch; one come after
                self.win.fit_size = (w, h)  # its watch ended, not)
                for tab in self.win.tabs.values():
                    tab.web.follow()
            return answer({**self.describe(t), "size": None})
        self.win.page_size, self.win.fit_size = (w, h), None  # (the Bot's own: kept, a watcher's given up)
        for tab in self.win.tabs.values():
            tab.web.follow()
        shown = t.place.get_mapped()
        GLib.timeout_add(300, guarded(lambda: self.js(t, "return {width: innerWidth, height: innerHeight}", answer,
                                                      lambda v: answer({**self.describe(t), "size": v, **(
                                                          {"note": "its page is shown in Baude's Browser page now, at "
                                                                   "the page's size: it's laid out at this size again "
                                                                   "once it isn't"} if shown else {})}))
                                      and False, answer))

    def resize_window(self, msg, answer):
        """The page's size (its viewport) made width by height, CSS pixels. fit: a watcher's (its pane's size), the
        size the Bot had kept to put back once no one watches; else the Bot's own, kept."""
        t = self.tab(msg)
        if self.win.agent:  # (as it's done, in its turn: not as it was asked)
            if not msg.get("fit"):
                self.win.watch_size = None
            elif self.win.watch_size is None:
                self.win.watch_size = self.win.get_default_size()
        w, h = int(msg.get("width") or 0), int(msg.get("height") or 0)
        if not (200 <= w <= SCREEN[0] - 200 and 200 <= h <= SCREEN[1] - 300):
            raise ValueError(f"width between 200 and {SCREEN[0] - 200}, height between 200 and {SCREEN[1] - 300}")

        def sized(now):
            ww, wh = self.win.get_width() or self.win.get_default_size()[0], \
                self.win.get_height() or self.win.get_default_size()[1]
            iw, ih = int(now["w"]), int(now["h"])
            if iw < 1 or ih < 1:  # (not laid out yet: the window's all page)
                iw, ih = ww, wh
            self.win.set_default_size(max(200, ww + w - iw), max(200, wh + h - ih))
            GLib.timeout_add(500, guarded(lambda: self.js(t, "return {width: innerWidth, height: innerHeight}",
                                                          answer, lambda v: answer({**self.describe(t), "size": v}))
                                          and False, answer))
        self.js(t, "return {w: innerWidth, h: innerHeight}", answer, sized)

    def op_form_input(self, msg, answer):
        t = self.tab(msg)
        if not msg.get("ref"):
            raise ValueError("form_input needs the field's ref")
        self.js(t, f"const el = em.get({json.dumps(msg['ref'])}), v = {json.dumps(msg.get('value'))};"
                "if (el.tagName === 'SELECT') {"
                "  const o = [...el.options].find(o => o.value === String(v) || o.text.trim() === String(v));"
                "  if (!o) throw new Error('no option ' + v + ': ' + [...el.options].map(o => o.text.trim()).join(', '));"
                "  em.setValue(el, o.value); return o.text.trim(); }"
                "if (el.type === 'checkbox' || el.type === 'radio') {"
                "  const want = v === true || v === 'true' || v === 'on' || v === 1;"
                "  if (el.checked !== want) el.click(); return el.checked; }"
                "if (!['INPUT', 'TEXTAREA'].includes(el.tagName)) throw new Error(" + json.dumps(str(msg["ref"])) +
                " + ' is a ' + el.tagName.toLowerCase() + ', not a field');"
                "em.setValue(el, String(v ?? '')); return el.value", answer,
                lambda value: answer({**self.describe(t), "value": value}))

    # (a page's own prompts: alert, confirm, prompt, a site's sign-in, files to upload)

    def op_prompts(self, msg, answer):
        answer({"prompts": self.win.prompts(None)})

    def op_answer_prompt(self, msg, answer):
        self.win.answer_prompt(msg)
        GLib.timeout_add(300, lambda: answer({"ok": True, "tabs": [self.describe(t) for t in self.win.tabs.values()]})
                         and False)

    # (sign-ins)

    def op_sites(self, msg, answer):
        self.win.Engine.cookies(self.win.profile_id, lambda cookies, why: answer(
            {"error": why} if why else {"profile": self.win.profile, "sites": sites_of(cookies)}))

    def op_add_cookies(self, msg, answer):
        self.win.Engine.add_cookies(self.win.profile_id, msg.get("cookies") or [],
                                    lambda n, why: answer({"error": why} if why else {"added": n}))


def sites_of(cookies):
    """The sites cookies are for (signed in to, as like as not), from their domains."""
    out = set()
    for c in cookies or []:
        d = str(c.get("domain") or "").lstrip(".").lower()
        if d:
            parts = d.split(".")
            out.add(".".join(parts[-3:]) if len(parts) > 2 and len(parts[-2]) <= 3 else ".".join(parts[-2:]))
    return sorted(out)


class Watcher:
    """Someone watching a profile's browser (Baude's Browser pane): its frames when the page changes, and its state
    (tabs, prompts) when that does, each frame sent only once the last's been taken (acked): a slow one gets fewer,
    never a backlog. shm (one on this machine): the frame's pixels in shared memory, only a line saying so sent;
    else (one elsewhere, through its host) the frame as a JPEG in the line itself."""
    ACK_S = 5  # (a frame not acked in this long: taken as lost)

    def __init__(self, win, conn, msg):
        self.win, self.conn = win, conn
        self.shm_wanted = bool(msg.get("shm"))
        self.max_w = max(100, min(3840, int(msg.get("max_w") or 1280)))
        self.max_h = max(100, min(2400, int(msg.get("max_h") or 900)))
        self.seq, self.waiting_since, self.sent_sum, self.sent_state, self.pending = 0, None, None, None, None
        self.memory, self.buffer, self.out, self.writing = None, b"", b"", None
        conn.setblocking(False)
        self.source = GLib.io_add_watch(conn.fileno(), GLib.PRIORITY_DEFAULT,
                                        GLib.IOCondition.IN | GLib.IOCondition.HUP | GLib.IOCondition.ERR, self.heard)

    def heard(self, _fd, cond):
        """What the watcher says: {ack: seq} for a frame taken, {max_w, max_h} for its new size; or it's gone."""
        try:
            part = self.conn.recv(1 << 16)
        except BlockingIOError:
            return True
        except OSError:
            part = b""
        if not part:
            self.win.unwatch(self)
            return False
        self.buffer += part
        while b"\n" in self.buffer:
            line, self.buffer = self.buffer.split(b"\n", 1)
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if msg.get("ack") == self.seq:
                self.waiting_since = None
                if self.pending is not None:  # (the newest frame, taken while it waited: now)
                    self.offer(*self.pending)
            if msg.get("max_w"):
                self.max_w = max(100, min(3840, int(msg["max_w"])))
                self.max_h = max(100, min(2400, int(msg.get("max_h") or self.max_h)))
                self.sent_sum = None  # (sent again at its new size)
                self.win.watch_kick()  # (an ack isn't the page changing: only a new size is)
        return True

    def say(self, msg):
        """msg sent without waiting on the watcher: what the socket won't take now is kept, and written as it will
        (a frame isn't made till it's all gone). False if it's gone."""
        self.out += (json.dumps(msg) + "\n").encode()
        return self.flush()

    def flush(self, *_):
        try:
            while self.out:
                n = self.conn.send(self.out)
                self.out = self.out[n:]
        except BlockingIOError:
            if self.writing is None:
                self.writing = GLib.io_add_watch(self.conn.fileno(), GLib.PRIORITY_DEFAULT, GLib.IOCondition.OUT,
                                                 self.flushed)
            return True
        except (OSError, ValueError):
            self.win.unwatch(self)
            return False
        return True

    def flushed(self, *_):
        self.writing = None
        self.flush()
        return False

    def ready(self):
        if self.out:
            return False
        if self.waiting_since is not None and time.monotonic() - self.waiting_since > self.ACK_S:
            self.waiting_since = None
        return self.waiting_since is None

    def offer(self, w, h, stride, data, total):
        """A frame (the page's, w by h): sent if it's new to this watcher and it's taken the last; else kept, the
        newest, to be sent as soon as it has."""
        if total == self.sent_sum:
            self.pending = None  # (back as it was sent: nothing newer to send)
            return
        if not self.ready():
            self.pending = (w, h, stride, data, total)
            return
        self.pending = None
        if w > self.max_w or h > self.max_h:
            w, h, stride, data = scaled(w, h, stride, data, self.max_w, self.max_h)
        self.seq += 1
        if self.shm_wanted and self.to_memory(stride * h):
            self.memory.buf[:stride * h] = data
            line = {"frame": self.seq, "w": w, "h": h, "stride": stride, "shm": self.memory.name}
        else:
            line = {"frame": self.seq, "w": w, "h": h, "jpeg": base64.b64encode(jpeg(w, h, stride, data)).decode()}
        if self.say(line):
            self.sent_sum, self.waiting_since = total, time.monotonic()

    def to_memory(self, size):
        """Shared memory at least size big (made anew if it's smaller): False if there's none to be had."""
        if self.memory is not None and self.memory.size >= size:
            return True
        from multiprocessing import shared_memory
        self.drop_memory()
        try:
            self.memory = shared_memory.SharedMemory(create=True, size=size + (size >> 2), track=False)
        except TypeError:  # (a Python before 3.13: no track)
            self.memory = shared_memory.SharedMemory(create=True, size=size + (size >> 2))
        except OSError:
            self.memory, self.shm_wanted = None, False
            return False
        return True

    def drop_memory(self):
        if self.memory is not None:
            try:
                self.memory.close()
                self.memory.unlink()
            except (OSError, BufferError):
                pass
            self.memory = None

    def close(self):
        GLib.source_remove(self.source)
        if self.writing is not None:
            GLib.source_remove(self.writing)
            self.writing = None
        self.drop_memory()
        try:
            self.conn.close()
        except OSError:
            pass


def scaled(w, h, stride, data, max_w, max_h):
    """Pixels made to fit max_w by max_h, their shape kept: (w, h, stride, data)."""
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf
    pic = GdkPixbuf.Pixbuf.new_from_bytes(GLib.Bytes.new(data), GdkPixbuf.Colorspace.RGB, True, 8, w, h, stride)
    scale = min(max_w / w, max_h / h)
    pic = pic.scale_simple(max(1, int(w * scale)), max(1, int(h * scale)), GdkPixbuf.InterpType.BILINEAR)
    return pic.get_width(), pic.get_height(), pic.get_rowstride(), pic.read_pixel_bytes().get_data()


def jpeg(w, h, stride, data, quality=wire.CONSTANTS["FRAME_QUALITY"]):
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf
    pic = GdkPixbuf.Pixbuf.new_from_bytes(GLib.Bytes.new(data), GdkPixbuf.Colorspace.RGB, True, 8, w, h, stride)
    flat = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, w, h)
    flat.fill(0xffffffff)
    pic.composite(flat, 0, 0, w, h, 0, 0, 1, 1, GdkPixbuf.InterpType.NEAREST, 255)
    return flat.save_to_bufferv("jpeg", ["quality"], [str(quality)])[1]
