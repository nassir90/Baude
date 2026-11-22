"""metacards — MetaChat's cards (MetaCards): a call that changed things, a tool call as Baude's chat shows one,
what you said, the reply as it comes, and the asks it makes on the way (a read to allow, a change to confirm).
"""
import html
import json
import os
import threading
import time
import urllib.parse
import weakref

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk, Pango  # noqa: E402

import lookupgit  # noqa: E402
import update  # noqa: E402
import widgets  # noqa: E402
import refkinds  # noqa: E402
import markdownview  # noqa: E402
import chatparts  # noqa: E402
from baudelib import wording  # noqa: E402
from meta import (  # noqa: E402
    CODE_LINES, COMMAND_TOOLS, EDIT_TOOLS, NAME, READ_TOOLS, REF_COLOR, REF_RE, api_doc, auto_mode_note, available,
    code_mode, earlier_said, effort, label, model, model_name, runs_commands, said_value, trimmed,
)
from metaagent import (  # noqa: E402
    Agent, CODE_SYSTEM, COMMANDS_SYSTEM, CONFIRM_WAIT, RUN_PYTHON, SETUP_NAMES, SETUP_TOOLS, SHARED_TOOLS, SYSTEM,
)
from metalook import SaidLabel, action_parts, paint_label, rows_group  # noqa: E402


class MetaCards:
    """MetaChat's cards: a call that changed things, a tool call, what you said, and the reply as it comes."""

    # ---- a call that changed things: a box with the tool card's outline, what it changed inside
    def call_widget(self, it):
        """What a tool's call shows in the reply: a setting changed, the setting's own row in an outlined box
        (setting_box); pages or chats changed, a box listing them (change_box); show's UI alone (add_shown); else a
        tool card. Returns (the widget or None, whether it holds what the call showed)."""
        base = (it.get("name") or "").removeprefix("mcp__meta__")
        acts = {a["id"]: a for a in self.actions()}
        batch = [acts[i] for i in it.get("actions") or () if i in acts]
        if it.get("state") != "error":
            if base == "set_setting" and it.get("shown") and it["shown"][0].get("name") in self.settings:  # (one
                return self.setting_box(it, batch), True  # that's gone since (an app's): its card)
            if base in ("show", "open_form", "fill_form"):  # (what they bring up speaks for itself)
                return None, False
            if any(a.get("marks") for a in batch):
                return self.change_box(batch, it), False
        return self.tool_card(it), False

    def call_hunks(self, it):
        """What a call changed (or proposes to) in some text, as [(what it replaces, what it puts in)]: each an edit's
        hunk on its card (Record's edits to a page, RecordChat)."""
        return []

    def call_row(self, it):
        """The left bar's row whose text a call's hunks change, (kind, key): in a change's box, they go under it."""
        return None

    def hunks(self, it, batch=()):
        """A call's hunks (call_hunks) as Baude's chat shows an Edit's: what it replaces on red, line by line, then
        what it puts in on green, wrapped as prose, faded once its batch is undone; None for a call with none, or
        that failed."""
        if it.get("state") == "error" or not (pairs := self.call_hunks(it)):
            return None
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=6)
        for old, new in pairs:
            box.append(markdownview.diff_block(old, new, wrap=True))
        if batch and all(a["state"] != "done" for a in batch):
            box.add_css_class("dim-label")
        return box

    def outline(self, batch, caption):
        """A box with a tool card's outline (theming.CSS's .tool-card): what was done, in a line, with the batch's Keep
        and Undo (or Redo) at its end; the pointer over it lights what it changed (light)."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.add_css_class("tool-card")
        line = Gtk.Box(spacing=8)
        said = Gtk.Label(label=caption, xalign=0, hexpand=True, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR)
        if batch and all(a["state"] != "done" for a in batch):
            said.add_css_class("dim-label")
            said.set_label(wording.say("meta_actions.undone", action=caption))
        line.append(said)
        if batch:
            line.append(self.batch_controls(batch))
            hover = Gtk.EventControllerMotion()
            hover.connect("enter", lambda *_: self.light(batch, True))
            hover.connect("leave", lambda *_: self.light(batch, False))
            box.add_controller(hover)
        box.append(line)
        return box

    def setting_box(self, it, batch):
        """set_setting's: the setting's own row, as Meta shows Settings' rows (shown_widget: hollow, outlined), to go on
        adjusting; on it, what it was before (its subtitle, if it has none of its own) and the change's Undo (Redo)."""
        group = self.shown_widget(it["shown"][0])
        if batch and (row := getattr(group, "row", None)) is not None:
            a = batch[0]
            row.set_tooltip_text(wording.say("meta_actions.by_meta", action=a["label"]))
            if not row.get_subtitle():
                row.set_subtitle(wording.say("meta_actions.was", value=said_value(a["undo"]["value"]))
                                 if a["state"] == "done" else
                                 wording.say("meta_actions.set_undone", value=said_value(a["redo"]["value"])))
            row.add_suffix(self.batch_controls(batch))
        return group

    def change_box(self, batch, it=None):
        """Pages or chats a call changed: what it did, and each one it changed, highlighted as the left bar has it
        (green added, red deleted, yellow archived, blue moved), clicked to open (open_mark); the call's (it) hunks
        under the one whose text they change (call_row), or under them all."""
        box = self.outline(batch, "; ".join(a["label"] for a in batch))
        items = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        row, under = self.call_row(it) if it is not None else None, None
        for a in batch:
            live = a["state"] == "done" and not a.get("kept")
            for kind, key, how, *note in a.get("marks") or ():
                line = Gtk.Box(spacing=6)
                name = Gtk.Label(label=self.mark_name(kind, key, a), xalign=0, ellipsize=Pango.EllipsizeMode.END)
                line.append(name)
                if note and note[0]:
                    said = Gtk.Label(label=note[0], xalign=0)
                    said.add_css_class("meta-note")
                    line.append(said)
                item = Gtk.Button(child=line, halign=Gtk.Align.START,
                                  tooltip_text=wording.say("meta.open_item", name=name.get_label()))
                item.add_css_class("flat")
                item.add_css_class("meta-item")
                item.connect("clicked", lambda *_, k=kind, key=key, how=how, a=a: self.open_mark(k, key, how, a))
                if live:
                    paint = lambda lit=False, n=name, how=how: paint_label(n, how, lit)  # noqa: E731
                    paint()
                    self.painted.setdefault((kind, key), {})[f"meta:{a['id']}"] = paint
                items.append(item)
                if under is None and row is not None and row in ((kind, key), (kind, note[1] if note[1:] else None)):
                    under = item  # (by its name now, or the one it had: moved since, in the same change)
        box.append(items)
        if it is not None and (hunks := self.hunks(it, batch)) is not None:
            if under is not None:
                hunks.set_margin_bottom(6)
                items.insert_child_after(hunks, under)
            else:
                box.append(hunks)
        return box

    def mark_name(self, kind, key, action):
        """What a changed chat or page is called, in a change's box."""
        if kind == "chat":
            chat = self.find_chat(key)
            ghost = next((i for p in action_parts(action) for i in (p.get("redo") or {}).get("infos") or ()
                          if i.get("id") == key), None)
            return chat["title"] if chat else (ghost or {}).get("title") or key[:8]
        return str(key).rpartition("\n")[2]

    def open_mark(self, kind, key, how, action):
        """A changed chat or page clicked, in a change's box: opened (each app's own)."""

    # ---- a tool call, as Baude's chat shows one
    def tool_title(self, name):
        """A tool's name as its card says it: Claude Code's as they are (Read), Meta's own and the mesh's in words (Move
        pages, Start subagent)."""
        name = name.removeprefix("mcp__meta__").removeprefix("mcp__mesh__")
        return name if name in READ_TOOLS + COMMAND_TOOLS else name.replace("_", " ").capitalize() if name else \
            wording.say("meta.tool_changes")

    def tool_summary(self, name, inp):
        """What a call of a tool was for, in a line: a file read, a pattern, a page, the chats, a setting and its
        value, the code's first line."""
        name = name.removeprefix("mcp__meta__")
        short = lambda v: ", ".join(map(str, v)) if isinstance(v, list) else str(v)  # noqa: E731
        if name in ("Read", "Edit", "Write"):
            return os.path.basename(str(inp.get("file_path") or "").rstrip(os.sep))
        if name in ("Grep", "Glob"):
            return str(inp.get("pattern") or "")
        if name == "Bash":
            return next((line for line in str(inp.get("command") or "").splitlines() if line.strip()), "")
        if name == "WebFetch":
            return str(inp.get("url") or "")
        if name == "WebSearch":
            return str(inp.get("query") or "")
        if name == "run_python":
            return next((line for line in str(inp.get("code") or "").splitlines() if line.strip()), "")
        if name == "set_setting":
            return f"{inp.get('name')} → {said_value(inp.get('value'))}"
        if name == "show":
            return " · ".join(filter(None, [str(inp.get("what") or ""), short(inp.get("items") or [])]))
        return " · ".join(short(v) for v in inp.values() if v not in (None, "", []))[:200]

    def tool_card(self, it, controls=True):
        """A tool card as Baude's chat has one (theming.CSS's .tool-card): its name, what it was for, an edit's hunks
        under its line (hunks) or else Input (Code) to open, and Output; a spinner while it runs, red if it failed. A
        call that changed things: its actions one batch, said undone once they are, and (controls) Keep and Undo
        (Redo, undone) on its line, and the pointer over it lights what it changed in the left bar (light)."""
        name = it.get("name") or ""
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        card.add_css_class("tool-card")
        if it.get("state") in ("error", "interrupted"):  # (one stopped part way: red, as a failed one)
            card.add_css_class("error")
        grid, line = Gtk.Grid(), Gtk.Box(spacing=8)
        title = Gtk.Label(xalign=0)
        title.set_markup(f"<b>{html.escape(self.tool_title(name))}</b>")
        toggles = Gtk.Box(spacing=4)
        header = Gtk.Label(label=self.tool_summary(name, it.get("input") or {}), xalign=0, hexpand=True,
                           ellipsize=Pango.EllipsizeMode.END)
        if it.get("state") == "interrupted":
            header.set_label(wording.say("meta.call_interrupted", call=header.get_label()) if header.get_label() else
                             wording.say("meta.interrupted"))
        for w in (title, toggles, header):
            line.append(w)
        grid.attach(line, 0, 0, 1, 1)
        card.append(grid)
        base = name.removeprefix("mcp__meta__")
        acts = {a["id"]: a for a in self.actions()}
        batch = [acts[i] for i in it.get("actions") or () if i in acts]
        if (hunks := self.hunks(it, batch)) is not None:  # (what it was given, as the change it makes)
            grid.attach(hunks, 0, 1, 1, 1)
        elif it.get("input"):
            code = base == "run_python"
            shown = str(it["input"].get("code") or "") if code else json.dumps(it["input"], ensure_ascii=False, indent=1)
            self._disclosure(toggles, grid, 1, wording.say("meta.code") if code else wording.say("meta.input"), shown,
                             "python" if code else "json")
        if it.get("result"):
            self._disclosure(toggles, grid, 2, wording.say("meta.error") if it.get("state") == "error" else
                             wording.say("meta.output"), it["result"], "output")
        if batch:
            card.set_tooltip_text("\n".join(a["label"] for a in batch))
            if all(a["state"] != "done" for a in batch):
                header.add_css_class("dim-label")
                header.set_label(wording.say("meta_actions.undone", action=header.get_label()))
        if batch and controls:
            line.append(self.batch_controls(batch))
            hover = Gtk.EventControllerMotion()
            hover.connect("enter", lambda *_: self.light(batch, True))
            hover.connect("leave", lambda *_: self.light(batch, False))
            card.add_controller(hover)
        self.card_extra(it, line, hunks)
        if (status := widgets._card_status(it.get("state") or "done")) is not None:
            line.append(status)
        return card

    def card_extra(self, it, line, hunks):
        """What an app adds to a call's card, on its line (hunks: what hunks shows under it, or None): Record's, an
        edit it proposed, its Accept and Reject while it waits."""

    @staticmethod
    def _disclosure(toggles, grid, row, label_, text, lang):
        """A toggle on a card's line opening what it was given or gave back, made when it's first opened."""
        d = widgets.Disclosure(toggles, grid, row, label_, button=True)

        def build():
            if d.get_child() is None:
                block = markdownview.CodeBlock(text, lang)
                block.set_margin_top(6)
                d.set_child(block)
        d.on_expand.append(build)
        return d

    def batch_controls(self, batch):
        """A call's actions, one batch: Keep (its marks in the left bar gone) while any's marked, and Undo, all of
        them; Redo, all of them, once they're undone. Never one of them alone: they were done together."""
        box = Gtk.Box(spacing=2, valign=Gtk.Align.CENTER)
        done = [a for a in batch if a["state"] == "done"]
        buttons = []
        if any(not a.get("kept") for a in done):
            buttons.append(("object-select-symbolic", wording.say("meta.keep"), lambda: self.keep(done)))
        if done:
            buttons.append(("edit-undo-symbolic", wording.say("common.undo"), lambda: self.undo_many(batch, True)))
        else:
            buttons.append(("edit-redo-symbolic", wording.say("common.redo"), lambda: self.redo_many(batch, True)))
        for icon, tip, fn in buttons:
            btn = Gtk.Button(icon_name=icon, tooltip_text=tip, valign=Gtk.Align.CENTER)
            btn.add_css_class("flat")
            btn.add_css_class("meta-batch")
            btn.connect("clicked", lambda *_, fn=fn: fn())
            box.append(btn)
        return box

    def light(self, batch, on):
        """What a batch of actions changed, lit in the left bar (on) or as it was, as the pointer goes over its card."""
        for a in batch:
            if a["state"] != "done" or a.get("kept"):
                continue
            for kind, key, *_rest in a.get("marks") or ():
                for paint in list(self.painted.get((kind, key), {}).values()):
                    paint(on)

    def shown_widget(self, spec):
        """What show put under a reply, made from what's kept of it: a setting's row, rows of chats or pages, the
        changelog, a button."""
        kind = spec.get("kind")
        if kind == "form":
            return self.form_slot(spec)
        if kind == "setting":
            s = self.settings[spec["name"]]
            group = Adw.PreferencesGroup()
            group.add_css_class("meta-ui")
            group.row = s.row()  # (Settings' own, in Meta's look: CSS's .meta-ui)
            group.add(group.row)
            return group
        if kind == "chats":
            return rows_group(self.chats_rows(spec.get("items") or []), limit=12)
        if kind == "pages":
            return rows_group(self.pages_rows(spec.get("items") or []), limit=12)
        if kind == "changelog":
            group = update.changelog_group(update.STARTED, limit=5)
            group.add_css_class("meta-ui")
            return group
        if kind == "button":
            btn = self.button(spec["action"], spec.get("items") or [])
            btn.set_halign(Gtk.Align.START)
            btn.add_css_class("meta-ui")
            return btn
        return None

    def _said(self, m):
        """What you wrote, its @references in it as a page draws an @name: the logo where its @ is (LinkLabel draws
        it), the name in the @names' colour on their patch, a pull request or an issue by its number alone;
        clicked, opened as one on a page is."""
        refs, text = {r["surface"]: r for r in m.get("refs") or ()}, self.plain(m["text"])
        out, at = [], 0
        for found in REF_RE.finditer(text):
            r = refs.get(found.group(1) or found.group(2))
            if r is None:
                continue
            github = lookupgit.GITHUB_NAME.fullmatch(r["surface"]) \
                if (r.get("icon") or "").startswith(("pr_", "issue")) else None
            name = html.escape(github[2] if github else r["surface"], False)
            out += [html.escape(text[at:found.start()], False),
                    f'<a href="ref:{urllib.parse.quote(r["surface"], safe="")}"><span foreground="{REF_COLOR}" '
                    f'background="#4f8a7c" bgalpha="14%" weight="600" underline="none">'
                    f'{refkinds.ref_icon_slot(r.get("icon")) or "@"}{name}</span></a>']
            at = found.end()
        out.append(html.escape(text[at:], False))
        said = SaidLabel(xalign=0.0, wrap=True, selectable=True)
        said.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        said.add_css_class("said-refs")
        said.set_markup("".join(out))
        self.linked(said)
        return said

    def plain(self, text):
        return text

    def linked(self, lb):
        """A label with names in it, its links followed as the app follows them (Record's: open_ref)."""

    def referenced(self, text):
        """The things a message @-references that space() knows, each once, in order."""
        out, seen, space = [], set(), self.space()
        for m in REF_RE.finditer(self.plain(text)):
            item = space.find(m.group(1) or m.group(2)) if space is not None else None
            if item is not None and item["surface"] not in seen:
                seen.add(item["surface"])
                out.append(item)
        return out

    def make_message(self, text):
        """A message as it's kept: what was written, and what it references."""
        msg = {"role": "user", "text": text, "t": time.time()}
        if refs := self.referenced(text):
            msg["refs"] = [{"surface": it["surface"], "what": f"{it['what']}, {it['summary']}", "icon": it.get("icon")}
                           for it in refs]
        return msg

    def system(self, tools):
        """Its instructions: Meta's, the app's, and in code mode app's functions."""
        text = SYSTEM.format(name=NAME, app=self.APP, what=self.WHAT, listed=self.LISTED,
                             and_pages=" and pages" if self.PAGES else "")
        if extra := self.app_prompt():
            text += "\n\n" + extra
        if runs_commands():
            text += COMMANDS_SYSTEM
        if code_mode():
            text += CODE_SYSTEM.format(lines=CODE_LINES, doc=api_doc([t for t in tools if t["name"] not in SETUP_NAMES]))
        return text

    def all_tools(self):
        return SHARED_TOOLS + SETUP_TOOLS + self.app_tools()

    CLEAR = ("clear", "/clear")  # (asked alone: the conversation cleared, not sent)

    def submit(self):
        buf = self.composer.get_buffer()
        text = self.writing.text().strip()
        if not self.ask and not available():
            self.model_changed()  # (gone since it was picked: said so)
        if self.exhausted.on and not self.ask and text.lower() in self.CLEAR:  # (a way out of it, typed in its box)
            buf.set_text("")
            return self._clear()
        if self.ask or self.exhausted.on or not self.ready() or not (text or self.can_ask_empty()):
            return
        if text.lower() in self.CLEAR:
            buf.set_text("")
            return self._clear()
        if self.reverting is not None:
            self.commit_revert()
        self.before_ask()
        msg = self.make_message(text or self.empty_question())
        self.messages().append(msg)
        buf.set_text("")
        self.taken(msg)
        if self.lit:
            self.lit = {}
            self.marks_changed()
        key = (self.scope(), model(), effort(), code_mode(), runs_commands())
        if self.agent is not None and self.agent.key != key:
            self.close_agent()  # (another model, another scope: a session for them, told what was said)
        try:
            if self.agent is None:
                tools = self.all_tools()
                offered = [RUN_PYTHON, *SETUP_TOOLS] if code_mode() else tools  # (the slow ones beside its code)
                setup = self.agent_setup()
                self.agent = Agent(self, key, self.system(tools), offered, setup["cwd"], setup.get("roots") or (),
                                   model(), setup.get("exe"), setup.get("env_of"), effort(), commands=key[-1],
                                   protect=setup.get("protect") or ())
                self.agent_made(self.agent)
            turn = {"text": [], "items": [], "actions": [], "shown": [], "code": [], "streamed": False,
                    "msg": msg, "told": list(self.told()), **self.turn_extra(msg),  # (msg, told: kept, if not sent)
                    "carried": any(not m.get("error") for m in self.messages()[:-1])}  # (what came before: earlier_said)
            said, turn["brought"] = self.message_for(self.agent, msg)
            self.agent.ask(said, turn)
        except OSError as e:
            self.agent = None
            self.messages().append({"role": "meta", "text": wording.say("meta.couldnt_run", model=model_name(), why=e),
                                    "error": True, "t": time.time()})
            self.save(now=True)
            return self.render()
        self.ask = self.agent
        self.context_soon(self.agent)
        self.live_widget = self._live(turn)
        self.render()
        self._busy(True)
        self.save(now=True)

    def agent_made(self, agent):
        """A session just started for the conversation."""

    def can_ask_empty(self):
        return False

    def empty_question(self):
        return ""

    def before_ask(self):
        pass

    def taken(self, msg):
        """A message just sent: what went with it let go (Record's quote)."""

    def turn_extra(self, msg):
        return {}

    def message_for(self, agent, msg):
        """What's sent for a message: a new session's index, what was said before it and the app's first words
        (first_said); what the user's done since (undone, redone, a row adjusted); the app's own (context); and what
        was written. And what of it the user brought (all but Meta's own words and what was said before: Agent.first
        counts it)."""
        own = []
        if agent.fresh:
            own.append(f"<app_index>\n{self.index_text()}\n</app_index>")
            if earlier := earlier_said(self.messages()[:-1], where=self.said_where):
                own.append(earlier)
            own += self.first_said(agent)
        brought = [*self.since(), *self.context(agent, msg), msg["text"]]
        said = lambda parts: "\n\n".join(p.strip("\n") for p in parts if p)  # noqa: E731
        return said(own + brought), said(brought)

    def first_said(self, agent):
        """The app's own words with a new session's first message, before what the user brought (Record's: the notebook
        open): [text, …]."""
        return []

    def since(self):
        """What the user's done that Meta hasn't been told: its actions they undid or redid, and the settings they
        changed with the rows under its replies."""
        told = self.told()
        out = list(told)
        del told[:]
        for m in self.messages():
            specs = [s for it in m.get("items") or () if it["kind"] == "tool" for s in it.get("shown") or ()]
            for spec in specs or m.get("shown") or ():
                if spec.get("kind") != "setting" or "set" not in spec or spec["name"] not in self.settings:
                    continue
                now = self.settings[spec["name"]].value()
                if now != spec.get("told", spec["set"]):
                    out.append(f"(The user set {spec['name']} to {said_value(now)} with the row under your reply.)")
                    spec["told"] = now
        return out

    # ---- the reply as it comes
    def reply_text(self, text):
        """What of a reply's text is shown (Record's leaves out its Unknown: line)."""
        return text

    def _said_more(self, agent, turn, text):
        """More of what Meta's saying: the part it's on, or a new one after a tool's."""
        items = turn["items"]
        if items and items[-1]["kind"] == "text":
            items[-1]["text"] += text
        else:
            items.append({"kind": "text", "text": text})
        live = self.live_widget
        if agent is not self.ask or live is None:
            return
        shown = self.reply_text(items[-1]["text"]).strip()
        if live.parts == len(items) and live.prose is not None and shown:
            self.set_prose(live.prose, shown)  # (the part it's on, growing: only it's drawn again)
            GLib.idle_add(self._to_bottom)
        else:
            self._refresh_live(turn)

    def _tool_started(self, agent, turn, c):
        name, inp = c.get("name") or "", c.get("input") or {}
        made = next((it for it in turn["items"] if it["kind"] == "tool" and it.get("id") is None and it.get("input") == inp
                     and it["name"].removeprefix("mcp__meta__") == name.removeprefix("mcp__meta__")), None)
        if made is not None:  # (its call came in on the socket before Claude Code said it: that card's this one)
            made.update(id=c.get("id"), name=name)
        else:
            turn["items"].append({"kind": "tool", "id": c.get("id"), "name": name, "input": inp, "state": "running",
                                  "actions": [], "shown": []})
        if agent is self.ask:
            self._refresh_live(turn)

    def _tool_ended(self, agent, turn, c):
        item = next((it for it in turn["items"] if it["kind"] == "tool" and it.get("id") == c.get("tool_use_id")), None)
        if item is None:
            return
        content = c.get("content")
        text = content if isinstance(content, str) else "\n".join(
            b.get("text") or "" for b in content or () if isinstance(b, dict) and b.get("type") == "text")
        item["result"], item["state"] = trimmed(text, 4000), "error" if c.get("is_error") else "done"
        self.context_soon(agent)
        if agent is self.ask:
            self._refresh_live(turn)

    def _compaction(self, agent, turn, what, detail):
        """Claude Code compacting the conversation in a turn (chatparts.compact_said's what and detail): an item of the
        reply, {"kind": "compact", "state": running|done|failed, "written", "meta", "error", "summary"}, its marker
        (compact_mark); none, if it ended with none made."""
        items = turn["items"]
        item = items[-1] if items and items[-1]["kind"] == "compact" and \
            (items[-1]["state"] == "running" or what == "summary") else None
        if what == "succeeded" or what == "ended" and item is None:
            return
        if what == "ended":
            items.remove(item)
        elif what == "summary":
            item["summary"] = detail
        else:
            if item is None:
                item = {"kind": "compact", "state": "running"}
                items.append(item)
            item["state"] = what
            if detail is not None:
                item[{"running": "written", "done": "meta", "failed": "error"}[what]] = detail
        if agent is self.ask:
            self._refresh_live(turn)

    def compact_mark(self, it):
        """A reply's compaction (_compaction's item), as Baude's chat marks one."""
        mark = chatparts.CompactMark(it.get("meta") or {}) if it.get("state") == "done" else chatparts.CompactMark()
        if it.get("state") == "running":
            mark.running(it.get("written"))
        elif it.get("state") != "done":
            mark.fail(it.get("error"), stopped=it.get("state") == "interrupted")
        mark.set_summary(it.get("summary"))
        return mark

    def _delta(self, agent, text):
        """(Record's edits say themselves as their cards: nothing more to say here.)"""

    def _status(self, agent, said):
        """(What it's doing is its tool cards: nothing more to say here.)"""

    def _refresh_live(self, turn):
        """The reply coming, made again with what it's done so far: its parts, its tool cards, what they show."""
        if self.ask is None or self.live_widget is None or self.ask.turn is not turn:
            return
        old = self.live_widget
        self.live_widget = self._live(turn)
        if old.get_parent() is self.box:
            self.box.insert_child_after(self.live_widget, old)
            self.box.remove(old)
        GLib.idle_add(self._to_bottom)

    def _busy(self, busy):
        """A turn running or not: the box's stop button, and the working dots in the foot."""
        self.writing.set_busy(busy)
        self.dots.set_working(busy)

    def _live(self, turn):
        if not turn["items"]:
            reply = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, visible=False)  # (nothing yet: the dots say it's working)
            reply.bubble, reply.prose = reply, None
        else:
            reply = self._bubble({"role": "meta", "items": turn["items"]})
            last = turn["items"][-1]
            if last["kind"] != "text":
                reply.prose = None  # (the part to grow is a new one: made when it comes)
        reply.parts = len(turn["items"])
        return reply

    def ask_permission(self, agent, rid, name, inp):
        """A read outside its folders, not of anything referenced, or a command Auto mode held back: the user's asked,
        as Baude asks in its Manual mode, Allow or Deny."""
        home = lambda v: str(v or "").replace(os.path.expanduser("~"), "~", 1)  # noqa: E731
        code = None
        if name == "Bash":
            why = str(inp.get("description") or "").strip().rstrip(".")
            text = wording.say("meta_asks.run_why", why=why[:1].lower() + why[1:]) if why else \
                wording.say("meta_asks.run")
            code = trimmed(str(inp.get("command") or ""), 600)
        elif name in EDIT_TOOLS:
            path = home(inp.get("file_path"))
            text = wording.say("meta_asks.edit", path=path) if name == "Edit" else \
                wording.say("meta_asks.write", path=path)
            code = trimmed(str(inp.get("content") or ""), 600) if name == "Write" else trimmed("\n".join(
                [*(f"- {line}" for line in str(inp.get("old_string") or "").splitlines()),
                 *(f"+ {line}" for line in str(inp.get("new_string") or "").splitlines())]), 600)
        elif name == "WebFetch":
            text = wording.say("meta_asks.fetch", url=inp.get("url") or "")
        elif name == "WebSearch":
            text = wording.say("meta_asks.search_web", query=inp.get("query") or "")
        else:
            path = home(inp.get("file_path") or inp.get("path") or inp.get("pattern") or inp.get("glob"))
            text = wording.say("meta_asks.read", path=path) if name == "Read" else \
                wording.say("meta_asks.grep", path=path) if name == "Grep" else \
                wording.say("meta_asks.glob", path=path) if name == "Glob" else \
                wording.say("meta_asks.other", tool=name, path=path)
        if name in COMMAND_TOOLS and agent.started_mode not in (None, "auto"):
            text = wording.say("meta_asks.each_asked", note=auto_mode_note(agent.model), question=text)
        self._ask_card(text, wording.say("common.allow"), lambda allow: agent.answer(rid, allow, name, inp), code)
        self.tell_desktop(text)

    def tell_desktop(self, body):
        """A reply finished, or a question asked, while Meta's out of sight (its window not the one you're in, or its
        page not shown), as a desktop notification (widgets.desktop_notify): a click brings its window up, Meta shown.
        Taken back as Meta's shown, or its window's the one you're in with Meta in it (seen)."""
        root = self.get_root()
        if isinstance(root, Gtk.Window) and root.is_active() and self.get_mapped():
            return
        me = weakref.ref(self)
        if getattr(self, "seen_root", None) is not root:  # (once a window, its own as it's moved to another)
            if (old := getattr(self, "seen_handler", None)) is not None:
                old[0].disconnect(old[1])
                self.seen_handler = None
            if isinstance(root, Gtk.Window):
                self.seen_handler = (root, root.connect("notify::is-active",
                                                        lambda *_: (c := me()) is not None and c.seen()))
            self.seen_root = root
        if getattr(self, "seen_mapped", None) is None:
            self.seen_mapped = self.connect("map", lambda c: c.seen())

        def clicked():
            if (chat := me()) is not None and isinstance(win := chat.get_root(), Gtk.Window):
                win.present()
                if not chat.get_mapped() and hasattr(win, "show_meta"):
                    win.show_meta()
        widgets.desktop_notify(f"meta-{id(self)}", NAME, body, clicked)

    def seen(self):
        """Meta in sight: its notification gone."""
        root = self.get_root()
        if self.get_mapped() and isinstance(root, Gtk.Window) and root.is_active():
            widgets.withdraw_notification(f"meta-{id(self)}")

    def _ask_card(self, text, yes, answered, code=None):
        """A question to the user at the conversation's end, as Baude asks in its Manual mode: Deny, or yes (its
        button's label); answered(allow) once they say. code: a command it's about, under it in the code font. A turn
        stopped says Deny (_drop_asking)."""
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        card.add_css_class("tool-card")
        card.add_css_class("asking")  # (waiting on you: as a tool card asking in Baude's chat)
        card.append(label(text))
        if code:
            card.append(label(code, css=("monospace",), select=True))
        row = Gtk.Box(spacing=6, halign=Gtk.Align.END)

        def answer(allow):
            if card in self.asking:
                self.asking.remove(card)
            if card.get_parent() is not None:
                self.box.remove(card)
            answered(allow)
        for text_, allow in ((wording.say("common.deny"), False), (yes, True)):
            btn = Gtk.Button(label=text_)
            if allow:
                btn.add_css_class("suggested-action")
            btn.connect("clicked", lambda *_, allow=allow: answer(allow))
            row.append(btn)
        card.append(row)
        card.answer = answer
        self.asking.append(card)
        self.box.append(card)
        GLib.idle_add(self._to_bottom)
        return card

    def confirm(self, turn, text, yes):
        """The user asked (text, and yes: the button that says so) from a tool's work (Later: off the main loop, which
        draws the question): True once they say yes; a no, a turn stopped, or no answer in CONFIRM_WAIT, False."""
        box, said = {}, threading.Event()

        def ask():
            if self.agent is None or self.agent.turn is not turn or turn.get("stopped"):
                said.set()
            else:
                box["card"] = self._ask_card(text, yes, lambda allow: (box.update(yes=allow), said.set()))
                box["card"].confirm = True  # (dropped, however: a no, for the tool that waits on it)
                self.tell_desktop(text)
            return False
        GLib.idle_add(ask)
        if not said.wait(CONFIRM_WAIT) and box.get("card") is not None:
            GLib.idle_add(lambda: box["card"].answer(False) and False)
        return bool(box.get("yes"))

    def _drop_asking(self, deny=False):
        for card in list(self.asking):
            if deny or getattr(card, "confirm", False):
                card.answer(False)
            elif card.get_parent() is not None:
                self.box.remove(card)
        self.asking = []

    def finish(self, m, turn):
        """A reply kept: what the app adds to it (Record's: the edits it proposed)."""

    def worked(self, turn):
        return any(it["kind"] == "tool" for it in turn.get("items") or ())

    def _done(self, agent, text, error, turn, outgrown=False, need=None, small=False):
        """A turn's end; outgrown: the session said the conversation's more than its window holds; need: a new
        session's first message is (the tokens it'd take: Agent.first), and wasn't sent; small: as the session
        counts its instructions and tools, they're more than its window alone (TOO_SMALL). Either way, exhausted."""
        if agent is self.ask:
            self.ask, self.live_widget = None, None
            self._busy(False)
            self._drop_asking()
        if need is not None:
            self._unsent(turn)
            if agent is self.agent:
                self.too_small = agent.model if small else None
                self.show_usage({"totalTokens": need, "maxTokens": agent.window})
        elif outgrown and agent is self.agent:
            self.outgrew = agent
            self.show_usage({})
            agent.usage(lambda usage: agent is self.agent and self.show_usage(usage))
        elif agent is self.agent:
            self._context_now()  # (at once: the box off before another's asked, if it's over)
        if error or text.strip() or self.worked(turn):
            m = {"role": "meta", "text": text, "t": time.time(), "model": agent.model,
                 "items": [{k: v for k, v in it.items() if k != "ran"} for it in turn["items"]]}
            for it in m["items"]:
                if it.get("state") == "running":  # (stopped before its result came)
                    it["state"] = "interrupted"
            if turn.get("actions"):
                m["actions"] = turn["actions"]  # (undo's turn: true)
            if turn.get("warned"):
                m["warned"] = turn["warned"]
            self.finish(m, turn)
            if error:  # (what it did before the error: kept, its actions undone from its cards)
                m["text"], m["error"] = error, True
        else:
            m = None
        if m is not None:
            self.store_reply(agent, m)
            self.tell_desktop(wording.say("meta.ended_error", error=error) if error else
                              text.strip() or wording.say("meta.finished"))
        self.save(now=True)
        if self.showing(agent):
            self.render()
        return False

    def _unsent(self, turn):
        """A message that wasn't sent (more than its session's window holds): out of the conversation, its text back in
        the box, and what Meta was to be told with it kept for the next."""
        said, msg = self.messages(), turn.get("msg")
        if any(m is msg for m in said):
            said[:] = [m for m in said if m is not msg]
            buf = self.composer.get_buffer()
            if not buf.get_char_count():
                buf.set_text(msg["text"])
        told = self.told()
        told[:0] = [t for t in turn.get("told") or () if t not in told]

    def store_reply(self, agent, m):
        self.messages().append(m)

    def showing(self, agent):
        """Whether the conversation agent's in is the one shown."""
        return True
