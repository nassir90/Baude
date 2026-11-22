"""metatools — MetaChat's tools (MetaTools): running a call and journaling what it changed (undo, redo, keep),
the index Meta reads the app through, setting models up (SETUP_TOOLS) and the tools every app shares.
"""
import contextlib
import os
import re
import sys
import threading
import time
import urllib.parse
import uuid

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

import update  # noqa: E402
import widgets  # noqa: E402
from baudelib import modelfiles, wording  # noqa: E402
from baudelib import models as modelslib  # noqa: E402
from baudelib import providers as providerslib  # noqa: E402
from meta import (  # noqa: E402
    NAME, attr, check_code, label, process_rows, process_said, run_isolated, said_value, trimmed,
)
from metaagent import FETCHES, Later, SETUP_NAMES, ToolSocket, files_report, models_report  # noqa: E402
from metalook import bundle  # noqa: E402


class MetaTools:
    """MetaChat's tools: running them and journaling what they changed, the index, setting models up, and the
    shared tools."""

    # ---- its tools
    def _guard(self, agent):
        """Why agent's tools can't act now (an error to answer with), or None."""
        if agent is not self.agent or agent.turn is None:
            return {"error": "That conversation isn't open any more: nothing was done."}
        return None

    def _tool(self, agent, tool, args):
        """Meta's tools (ToolSocket), on the main loop: run_python's code's calls of app's too."""
        if (error := self._guard(agent)) is not None:
            return error
        if tool == "run_python":
            return {"error": "run_python's code can't run Python itself."}
        return self.run_tool(agent.turn, tool, args)

    def handlers(self):
        return {"app_index": self.app_index, "set_setting": self.set_setting, "show": self.show_ui,
                "read_chat": self.read_chat, "rename_chat": self.rename_chat, "undo": self.undo_tool,
                "redo": self.redo_tool, "open_form": self.open_form, "fill_form": self.fill_form,
                "local_models": self.local_models, "check_models": self.check_models,
                "prune_models": self.prune_models, "add_mlx_model": self.add_mlx_model,
                "add_endpoint": self.add_endpoint, "model_files": self.model_files, "fetch_model": self.fetch_model,
                "build_apple_adapter": self.build_apple_adapter,
                **self.app_handlers()}

    def run_tool(self, turn, tool, args):
        """One of Meta's tools, in a turn (turn: what its reply's made of, the actions it takes among it)."""
        handler = self.handlers().get(tool)
        if handler is None:
            return {"error": f"There's no tool {tool}."}
        turn["at"] = self._item_for(turn, tool, args)
        try:
            res = handler(turn, args)
        except ValueError as e:
            res = {"error": str(e)}
        self._refresh_live(turn)
        if isinstance(res, Later):  # (its card drawn again once it's answered; what it does then, only while its turn's
            done = res.done             # still the one going: stopped meanwhile, nothing's done)

            def finished(out):
                if done is not None and "error" not in out:
                    if self.agent is None or self.agent.turn is not turn or turn.get("stopped"):
                        return {"error": "Stopped: nothing was done."}
                    out = done(out)
                self._refresh_live(turn)
                return out
            res.done = finished
        return res

    @staticmethod
    def _item_for(turn, tool, args):
        """The card a call of Meta's tool is on: run_python's while its code runs; else the call Claude Code made
        that has yet to be answered (its arguments the same, else the first), or one made for it."""
        if turn.get("nested") is not None:
            return turn["nested"]
        waiting = [it for it in turn["items"] if it["kind"] == "tool" and not it.get("ran") and
                   it["name"].removeprefix("mcp__meta__") == tool and it.get("state") == "running"]
        item = next((it for it in waiting if it.get("input") == args), waiting[0] if waiting else None)
        if item is None:  # (the call's come in before Claude Code's said it: its card made now, _tool_started's)
            item = {"kind": "tool", "id": None, "name": tool, "input": args, "state": "running", "actions": [],
                    "shown": []}
            turn["items"].append(item)
        item["ran"] = True
        return item

    def act(self, turn, label_, kind, undo, redo, marks=()):
        """An action Meta's taken (done already): kept in the journal, under the reply it's in. undo, redo: what
        apply needs to take it back and do it again; marks: [kind, key, how, note, the key it had] for the left bar's
        rows it changed (added, removed, moved, archived, edited). A change to what the left bar lists joins the
        turn's change, if it's made one (bundle): a turn's changes to it are one action, kept, undone and done again
        as one; a setting's is one of its own."""
        marks = [list(m) for m in marks]
        joined = next((x for x in self.actions() if x["id"] == turn.get("bundle") and x["state"] == "done" and
                       not x.get("kept")), None) if marks else None  # (kept, or undone: what's next is new)
        if joined is not None:
            a = bundle(joined, label_, kind, undo, redo, marks)  # (the same action: what's drawn of it holds it)
            self.actions().remove(a)
            self.actions().append(a)  # (last: as it's the latest change, and where it's undone in order from)
        else:
            a = {"id": uuid.uuid4().hex[:6], "label": label_, "kind": kind, "undo": undo, "redo": redo,
                 "state": "done", "kept": not marks, "marks": marks, "t": time.time()}
            self.actions().append(a)
            if marks:
                turn["bundle"] = a["id"]
        if a["id"] not in turn["actions"]:
            turn["actions"].append(a["id"])
        if (item := turn.get("at")) is not None and a["id"] not in item.setdefault("actions", []):
            item["actions"].append(a["id"])
        self.save()
        if marks:
            self.marks_changed()
            self.show_changes_bar()
        return a

    def apply(self, kind, data):
        if kind == "setting":
            self.settings[data["name"]].set(data["value"])
        elif kind == "models":  # (an MLX model, or a server, added: in settings.json's list, or not, whatever else is)
            entry, field = data["entry"], "value" if data["key"] == "mlx_models" else "id"
            now = [x for x in widgets.load_settings().get(data["key"]) or []
                   if isinstance(x, dict) and x.get(field) != entry[field]]
            widgets.save_settings(**{data["key"]: now + [entry] if data["present"] else now})
            providerslib.found(max_age=0)
        elif kind == "rename_chat":
            chat = self.find_chat(data["chat"])
            if chat is None:
                raise ValueError("That chat isn't here any more.")
            self.rename_chat_to(chat, data["title"])
        else:
            self.apply_app(kind, data)

    def apply_action(self, a, undo):
        """An action undone (undo) or done again: a bundle's parts, the last first or in order, all of them or none
        (one that can't be: what was done before it put back as it was, and ValueError saying why)."""
        parts = a.get("parts") or [a]
        done = []
        try:
            for p in (reversed(parts) if undo else parts):
                self.apply(p["kind"], p["undo"] if undo else p["redo"])
                done.append(p)
        except (ValueError, KeyError, OSError) as e:
            for p in reversed(done):
                with contextlib.suppress(ValueError, KeyError, OSError):
                    self.apply(p["kind"], p["redo"] if undo else p["undo"])
            raise ValueError(str(e))

    def undo_action(self, a, by_user=False):
        return self.undo_many([a], by_user)

    def undo_many(self, acts, by_user=False):
        """Actions taken back, the last first; said to Meta with the next message if the user did it. What can't be
        (gone since) is left as it is, and said."""
        failed = []
        for a in reversed([a for a in acts if a["state"] == "done"]):
            try:
                self.apply_action(a, undo=True)
                a["state"] = "undone"
                if by_user:
                    self.told().append(f"(The user undid your action {a['id']}: {a['label']}.)")
            except (ValueError, KeyError, OSError) as e:
                failed.append(f"{a['label']}: {e}")
        self._journal_changed()
        if failed and by_user:
            widgets.toast(self, wording.say("meta_actions.couldnt_undo", failed="; ".join(failed)))
        return failed

    def redo_action(self, a, by_user=False):
        if a["state"] != "undone":
            return []
        try:
            self.apply_action(a, undo=False)
        except (ValueError, KeyError, OSError) as e:
            if by_user:
                widgets.toast(self, wording.say("meta_actions.couldnt_redo", action=a["label"], why=e))
            return [str(e)]
        a["state"], a["kept"] = "done", not a.get("marks")
        if by_user:
            self.told().append(f"(The user redid your action {a['id']}: {a['label']}.)")
        self._journal_changed()
        return []

    def redo_many(self, acts, by_user=False):
        """Actions done again, in the order they were first done."""
        failed = []
        for a in [a for a in acts if a["state"] == "undone"]:
            failed += self.redo_action(a, by_user)
        return failed

    def keep(self, acts):
        """Changes in the left bar kept: their marks gone (undo's still under their reply)."""
        for a in acts:
            a["kept"] = True
        self._journal_changed()

    def _journal_changed(self):
        self.save()
        self.marks_changed()
        self.render()

    def unkept(self):
        return [a for a in self.actions() if a["state"] == "done" and not a.get("kept")]

    def marks(self, kind):
        """{key: (how, note, action)} of a kind of row the left bar lists (chat, page) that Meta's changed and that
        aren't kept yet, and those it's highlighted (how "highlight", no action)."""
        out = {}
        for a in self.unkept():
            for k, key, how, *note in a.get("marks") or ():
                if k == kind:
                    out[key] = (how, note[0] if note else "", a)
        for key in self.lit.get(kind, ()):
            out.setdefault(key, ("highlight", "", None))
        return out

    def _changes_bar(self):
        """Over the box asked in, while changes Meta's made in the left bar aren't kept: how many, Keep and Undo."""
        bar = Gtk.Box(spacing=2)
        bar.add_css_class("changes-bar")
        self.changes_label = label("", css=("caption",))
        self.changes_label.set_hexpand(True)
        self.changes_label.set_valign(Gtk.Align.CENTER)
        bar.append(self.changes_label)
        for text, fn in ((wording.say("meta.keep"), lambda: self.keep(self.unkept())),
                         (wording.say("common.undo"), lambda: self.undo_many(self.unkept(), True))):
            btn = Gtk.Button(label=text, valign=Gtk.Align.CENTER)
            btn.add_css_class("flat")
            btn.connect("clicked", lambda *_, fn=fn: fn())
            bar.append(btn)
        bar.set_visible(False)
        return bar

    def show_changes_bar(self):
        n = len(self.unkept())
        self.changes_label.set_text(wording.say("meta.changes", n=n))
        self.changes_box.set_visible(n > 0)

    def find_action(self, ref):
        ref = str(ref or "").strip()
        acts = self.actions()
        if ref in ("", "last"):
            return next((a for a in reversed(acts) if a["state"] == "done"), None)
        return next((a for a in acts if a["id"] == ref), None)

    def undo_tool(self, turn, args):
        a = self.find_action(args.get("action"))
        if a is None:
            raise ValueError("No such action of yours: its id is in your replies.")
        acts = [a]
        if args.get("turn"):
            turns = [m.get("actions") or [] for m in self.messages()] + [turn["actions"]]
            ids = next((ids for ids in turns if a["id"] in ids), [a["id"]])
            acts = [x for x in self.actions() if x["id"] in ids]
        if all(x["state"] != "done" for x in acts):
            return {"text": "That's undone already."}
        failed = self.undo_many(acts)
        return {"text": f"Undone: {', '.join(x['label'] for x in acts if x['state'] == 'undone')}." +
                (f" Not undone: {'; '.join(failed)}." if failed else "")}

    def redo_tool(self, turn, args):
        a = self.find_action(args.get("action"))
        if a is None or a["state"] != "undone":
            raise ValueError("No action of yours with that id is undone.")
        failed = self.redo_action(a)
        return {"error": failed[0]} if failed else {"text": f"Done again: {a['label']}."}

    # ---- the index
    def shared_sections(self):
        def settings_rows(_f):
            return [{"name": s.name, "value": s.value(), "takes": s.takes(), "title": s.title, "note": s.note}
                    for s in self.settings.values()]

        def runtime_rows(_f):
            return [{**r, "what": "process"} for r in process_rows()] + \
                [{"what": "running", "said": line} for line in self.running_lines()]

        def changelog_rows(_f):
            return [{"commit": short, "when": update.made_when(stamp), "subject": subject}
                    for short, stamp, subject in update.changelog(update.STARTED)[:60]]

        def buttons_rows(_f):
            return [{"action": k, "does": v} for k, v in self.buttons().items()]

        def forms_rows(_f):
            return [{"form": k, "for": what} for k, (what, _make) in self.forms().items()]
        return {
            "settings": (settings_rows, lambda r: f"{r['name']} = {said_value(r['value'])} ({r['takes']}) · "
                                               f"{r['title']}" + (f" · {r['note']}" if r["note"] else "")),
            "runtime": (runtime_rows, lambda r: process_said(r) if r["what"] == "process" else r["said"]),
            "changelog": (changelog_rows, lambda r: f"{r['commit']} {r['when']} · {r['subject']}"),
            "buttons": (buttons_rows, lambda r: f"button:{r['action']} · {r['does']}"),
            "forms": (forms_rows, lambda r: f"{r['form']} · {r['for']}"),
        }

    def all_sections(self):
        return {**self.sections(), **self.shared_sections()}

    def section_rows(self, section, filter_=""):
        parts = self.all_sections()
        if section not in parts:
            raise ValueError(f"No part {section!r}: its parts are {', '.join(parts)}.")
        rows_of, line_of = parts[section]
        rows, f = rows_of(filter_), (filter_ or "").strip().lower()
        return [(r, line_of(r)) for r in rows if not f or f in line_of(r).lower()]

    def app_index(self, turn, args):
        rows = self.section_rows(str(args.get("section") or "").strip(), str(args.get("filter") or ""))
        text = "\n".join(line for _r, line in rows) or "(none)"
        return {"text": trimmed(text, 12000), "data": [r for r, _line in rows]}

    def index_text(self):
        """The light index: what's running and how big, the settings, the app's own lines, and its parts to open."""
        rows = process_rows()
        me = rows[0] if rows else None
        commit = (update.STARTED or "")[:7] or "?"
        head = f"{self.APP} {commit} · pid {os.getpid()}" + \
            (f" · {me['memory_mb']} MB · up {me['up']}" if me else "") + \
            f" · {len(rows) - 1} child processes" * bool(rows)
        settings = "settings: " + " ".join(f"{s.name}={said_value(s.value())}" for s in self.settings.values())
        lines = [head, settings, *self.index_lines()]
        running = self.running_lines()
        if running:
            lines.append("running: " + " · ".join(running))
        lines.append(f"parts (app_index): {', '.join(self.all_sections())}")
        return "\n".join(lines)

    # ---- setting models up (SETUP_TOOLS): what runs them here; Ollama's checked and pruned; an MLX model, an
    # OpenAI-compatible server added (actions, undone as settings are); Apple's adapter built
    def local_models(self, turn, args):
        return Later(lambda: {"text": models_report()})

    def ollama_names(self):
        """The models Ollama has, by name; ValueError if it isn't there."""
        p = providerslib.ollama()
        if p["error"]:
            raise ValueError(f"Ollama {p['error']}.")
        try:
            tags = providerslib.fetch(p["url"] + "/api/tags") or {}
        except (OSError, ValueError) as e:
            raise ValueError(f"Ollama didn't list its models: {e}")
        return {m.get("name"): m.get("size") or 0 for m in tags.get("models") or [] if isinstance(m, dict)}

    @staticmethod
    def ollama_named(asked, have):
        """Model names as Ollama has them (qwen3 for qwen3:latest)."""
        return list(dict.fromkeys(m if m in have or f"{m}:latest" not in have else f"{m}:latest" for m in asked))

    def check_models(self, turn, args):
        asked = [str(m) for m in args.get("models") or []]

        def work():
            have = self.ollama_names()
            names = self.ollama_named(asked, have)
            if missing := [m for m in names if m not in have]:
                raise ValueError(f"Ollama has no {', '.join(missing)}: it has {', '.join(have) or 'none'}.")
            lines, bad = [], 0
            for name in names or list(have):
                if self.agent is None or self.agent.turn is not turn or turn.get("stopped"):
                    lines.append("(stopped: the rest weren't checked)")
                    break
                verdict, said = providerslib.ollama_check(name)
                bad += verdict == "unhealthy"
                lines.append(f"{name}: {verdict}" + (f": {said}" if said else ""))
            return {"text": "\n".join(lines) + (f"\n{bad} unhealthy: prune_models removes them (the user's asked). "
                                               "An unsure one may be fine: say so, don't prune it." if bad else "")}
        return Later(work)

    def prune_models(self, turn, args):
        names = list(dict.fromkeys(str(m) for m in args.get("models") or []))
        if not names:
            raise ValueError("Name the models to remove.")

        def work():
            have = self.ollama_names()
            names[:] = self.ollama_named(names, have)
            if missing := [m for m in names if m not in have]:
                raise ValueError(f"Ollama has no {', '.join(missing)}: nothing was removed.")
            listed = ", ".join(f"{m} ({have[m] / 1e9:.1f} GB)" if have[m] else m for m in names)
            if not self.confirm(turn, wording.say("meta_asks.remove_models", n=len(names), models=listed),
                                wording.say("common.remove")):
                return {"text": "The user said no: nothing was removed."}
            done, failed = [], []
            for m in names:
                try:
                    providerslib.ollama_delete(m)
                    done.append(m)
                except OSError as e:
                    failed.append(f"{m} ({e})")
            providerslib.found(max_age=0)
            return {"text": (f"Removed {', '.join(done)}." if done else "Nothing was removed.") +
                    (f" Not removed: {'; '.join(failed)}." if failed else "")}
        return Later(work)

    def add_mlx_model(self, turn, args):
        repo = str(args.get("repo") or "").strip()

        def work():
            found = {m["repo"]: m for m in providerslib.mlx_downloaded()}
            if repo not in found:
                raise ValueError(f"No MLX model {repo!r} is downloaded: local_models lists those that are.")
            if found[repo]["added"]:
                raise ValueError(f"{repo} is one of MLX's models already.")
            return found[repo]

        def done(m):
            listed = modelslib.mlx_list()  # (listed as found already: its value kept, for the chats on it)
            was = next((x["value"] for x in listed if x["repo"] == repo and x.get("found")), None)
            taken = {x["value"] for x in listed if x["value"] != was}
            value = base = was or "mlx-" + re.sub(r"[^a-z0-9.]+", "-", repo.split("/")[-1].lower()).strip("-")
            n = 2
            while value in taken:
                value, n = f"{base}-{n}", n + 1
            shown = str(args.get("name") or "").strip() or repo.split("/")[-1]
            entry = {"value": value, "repo": repo, "displayName": shown,
                     "input": ["text", "image"] if m["vision"] else ["text"]} | \
                ({"thinking": True} if args.get("thinking") else {})
            self.apply("models", {"key": "mlx_models", "entry": entry, "present": True})
            a = self.act(turn, wording.say("meta_actions.added_mlx", model=shown), "models",
                         {"key": "mlx_models", "entry": entry, "present": False},
                         {"key": "mlx_models", "entry": entry, "present": True})
            return {"text": f"Added {shown} ({value}) to MLX's models (action {a['id']}): it's in the model pickers "
                    "now. MLX's server loads it when it's first asked."}
        return Later(work, done)

    def add_endpoint(self, turn, args):
        name, url = str(args.get("name") or "").strip(), str(args.get("url") or "").strip().rstrip("/")
        key_env = str(args.get("key_env") or "").strip().lstrip("$")
        window = args.get("window") if isinstance(args.get("window"), int) and args["window"] > 0 else None
        parts = urllib.parse.urlsplit(url)
        if not name or parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError("An endpoint needs a name, and its URL (http:// or https://, to its /v1).")
        if parts.username or parts.password or parts.query or parts.fragment:
            raise ValueError("Give the URL with no name, password or query in it: a key goes in key_env.")
        if key_env and (not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key_env) or key_env not in os.environ):
            raise ValueError("key_env is the name of an environment variable here that holds the key (not the key): "
                             f"there's no {key_env!r}.")
        base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:24] or "server"
        taken = {e.get("id") for e in widgets.load_settings().get("endpoints") or [] if isinstance(e, dict)}
        eid, n = base, 2
        while eid in taken:
            eid, n = f"{base}-{n}", n + 1
        ep = {"id": eid, "name": name, "url": url} | ({"key": f"${key_env}"} if key_env else {}) | \
            ({"window": window} if window else {})

        def work():
            if key_env or not providerslib.local_url(url):  # (a server elsewhere, or a key sent: the user's to say)
                where = parts.hostname + (f":{parts.port}" if parts.port else "")
                asked = wording.say("meta_asks.add_server_key", name=name, where=where, variable=key_env) if key_env else \
                    wording.say("meta_asks.add_server", name=name, where=where)
                if not self.confirm(turn, asked, wording.say("common.add")):
                    return {"text": "The user said no: nothing was added."}
            for try_url in [url] + ([url + "/v1"] if not url.endswith("/v1") else []):
                there = providerslib.endpoint(providerslib.endpoints({"endpoints": [ep | {"url": try_url}]})[0])
                if not there["error"]:
                    return {"url": try_url, "models": [m["resolvedModel"] for m in there["models"]]}
            raise ValueError(f"{name} {there['error']}: not added.")

        def done(found):
            if "url" not in found:
                return found
            entry = ep | {"url": found["url"]}
            self.apply("models", {"key": "endpoints", "entry": entry, "present": True})
            a = self.act(turn, wording.say("meta_actions.added_server", name=name), "models",
                         {"key": "endpoints", "entry": entry, "present": False},
                         {"key": "endpoints", "entry": entry, "present": True})
            listed = ", ".join(found["models"][:12]) or "none yet"
            return {"text": f"Added {name} at {found['url']} (action {a['id']}); its models: {listed}. It's a "
                    "provider in the pickers now."}
        return Later(work, done)

    def model_files(self, turn, args):
        model, host, kind = str(args.get("model") or "").strip(), str(args.get("host") or "").strip(), args.get("kind")

        def work():
            if modelfiles.is_here(host):
                errors = []
                for k in [kind] if kind else ["ollama", "mlx"]:
                    try:
                        where = modelfiles.ollama_where(model) if k == "ollama" else modelfiles.mlx_where(model)
                        return {"text": files_report(where, k, "this machine")}
                    except ValueError as e:
                        errors.append(str(e))
                raise ValueError("; ".join(errors))
            k, where = modelfiles.remote_where(modelfiles.member_of(host), model, kind)
            return {"text": files_report(where, k, host)}
        return Later(work)

    def fetch_model(self, turn, args):
        model, host, kind = str(args.get("model") or "").strip(), str(args.get("host") or "").strip(), args.get("kind")
        told = self.told()

        def work():
            member = modelfiles.member_of(host)
            k, where = modelfiles.remote_where(member, model, kind)
            key = (member, where.get("name") or where.get("repo") or model)
            if (FETCHES.get(key) or {}).get("state") in ("starting", "copying"):
                return {"text": f"{model} is being fetched from {host} already: local_models says how it's going."}
            if k == "ollama" and providerslib.ollama()["error"]:
                raise ValueError("Ollama isn't running here: the user opens it (or runs ollama serve) first.")
            mine = k == "ollama" and where["name"] in self.ollama_names()
            size = f"{where['size'] / 1e9:.1f}"
            asked = wording.say("meta_asks.copy_mlx", model=model, size=size, host=host, hub=modelslib.hf_hub()) \
                if k != "ollama" else \
                wording.say("meta_asks.copy_ollama_replacing", model=model, size=size, host=host, name=where["name"]) \
                if mine else wording.say("meta_asks.copy_ollama", model=model, size=size, host=host)
            if not self.confirm(turn, asked, wording.say("common.copy")):
                return {"text": "The user said no: nothing was copied."}
            f = FETCHES[key] = {"host": host, "model": model, "kind": k, "state": "starting", "done": 0,
                                "total": where["size"], "error": None}

            def progress(done, _total, _what):
                f.update(state="copying", done=min(done, f["total"]))

            def run():
                try:
                    got = modelfiles.ollama_fetch(member, model, progress) if k == "ollama" else \
                        modelfiles.mlx_fetch(member, model, progress)
                    f.update(state="done", done=f["total"])
                    providerslib.found(max_age=0)
                    note = f"(fetched {model} from {host}: " + (f"Ollama has it as {got}.)" if k == "ollama" else
                                                                f"in {got}; it's listed among MLX's models.)")
                except Exception as e:  # noqa: BLE001 (any way it ends: said, never left "copying")
                    f.update(state="failed", error=str(e) or type(e).__name__)
                    note = f"(fetching {model} from {host} failed: {f['error']})"
                GLib.idle_add(lambda: told.append(note) and False)
            threading.Thread(target=run, daemon=True, name="fetch-model").start()
            return {"text": f"Copying {model} from {host} in the background; you're told when it's done "
                            "(local_models says how it's going)."}
        return Later(work)

    def build_apple_adapter(self, turn, args):
        told = self.told()  # (this conversation's: another may be open when it's built)

        def work():
            st = providerslib.apple_status()
            if sys.platform != "darwin":
                raise ValueError("apple-claude runs on macOS only.")
            if st["built"]:
                return {"text": f"apple-claude is built already ({st['exe']})."}
            if st["build"] == "building":
                return {"text": "It's building already: local_models says how it's going."}
            if not st["fits"]:
                raise ValueError(f"Apple's on-device model needs macOS {'.'.join(map(str, providerslib.APPLE_NEEDS))} "
                                 f"or later; this is {st['macos'] or 'older'}.")
            if not st["swift"]:
                raise ValueError("There's no swift here: the user installs Xcode, or its command line tools "
                                 "(xcode-select --install), first.")
            if not self.confirm(turn, wording.say("meta_asks.build_apple"), wording.say("meta_asks.build")):
                return {"text": "The user said no: nothing was built."}

            def built(ok):  # (said with the next message of the conversation it was asked in)
                GLib.idle_add(lambda: told.append(
                    "(apple-claude " + ("built: Apple's model is in the pickers.)" if ok else
                                        "didn't build: local_models has its last lines.)")) and False)
            if not providerslib.apple_build(built):
                raise ValueError("It couldn't be started: local_models says why.")
            return {"text": "Building apple-claude in the background; you're told when it's done."}
        return Later(work)

    # ---- the shared tools
    def set_setting(self, turn, args):
        name = str(args.get("name") or "").strip()
        s = self.settings.get(name)
        if s is None:
            raise ValueError(f"No setting {name!r}: they're {', '.join(self.settings)}.")
        if not s.meta_sets:
            raise ValueError(f"{name} is the user's to change: show setting:{name} for them to.")
        before = s.value()
        now = s.set(args.get("value"))
        self._show(turn, {"kind": "setting", "name": name, "set": now})
        if now == before:
            return {"text": f"{name} is {said_value(now)} already. Its row is under your reply."}
        a = self.act(turn, wording.say("meta_actions.setting_changed", setting=s.title, before=said_value(before),
                                       after=said_value(now)), "setting",
                     {"name": name, "value": before}, {"name": name, "value": now})
        return {"text": f"{name} is {said_value(now)} now (action {a['id']})" + (f"; {s.note}" if s.note else "") +
                ". Its row is under your reply."}

    def show_ui(self, turn, args):
        what, items = str(args.get("what") or "").strip(), [str(i) for i in args.get("items") or []]
        kind, _, rest = what.partition(":")
        if kind == "setting":
            if rest not in self.settings:
                raise ValueError(f"No setting {rest!r}: they're {', '.join(self.settings)}.")
            spec = {"kind": "setting", "name": rest}
        elif kind in ("chats", "pages"):
            (self.chats_rows if kind == "chats" else self.pages_rows)(items)  # (raises if one isn't there)
            spec = {"kind": kind, "items": items}
        elif kind == "changelog":
            spec = {"kind": "changelog"}
        elif kind == "button":
            self.button(rest, items)
            spec = {"kind": "button", "action": rest, "items": items}
        else:
            raise ValueError("show takes setting:<name>, chats, pages, changelog or button:<action>.")
        self._show(turn, spec)
        return {"text": "Shown under your reply."}

    @staticmethod
    def _show(turn, spec):
        """UI under the card of the call that showed it."""
        turn["shown"].append(spec)
        if (item := turn.get("at")) is not None:
            item.setdefault("shown", []).append(spec)

    def _chat(self, ref):
        chat = self.find_chat(str(ref or "").strip())
        if chat is None:
            raise ValueError(f"No chat {ref!r}: app_index chats lists them, each with its id.")
        return chat

    def read_chat(self, turn, args):
        chat = self._chat(args.get("chat"))
        return {"text": f'<chat title="{attr(chat["title"])}">\n{self.chat_text(chat)}\n</chat>'}

    def rename_chat(self, turn, args):
        chat, title = self._chat(args.get("chat")), " ".join(str(args.get("title") or "").split())
        if not title:
            raise ValueError("A title is needed.")
        before = chat["title"]
        if title == before:
            return {"text": "It's called that already."}
        self.rename_chat_to(chat, title)
        a = self.act(turn, wording.say("meta_actions.renamed", before=before, after=title), "rename_chat",
                     {"chat": chat["key"], "title": before}, {"chat": chat["key"], "title": title},
                     marks=[("chat", chat["key"], "moved", wording.say("meta_actions.renamed_note"))]
                     if self.CHAT_ROWS else ())
        return {"text": f"Renamed (action {a['id']})."}

    def _python(self, agent, args):
        """run_python, on the socket's thread (ToolSocket): its code checked on the main loop and its card found
        (_python_start), run in a Python of its own (run_isolated) whose calls to app the main loop answers as it's
        free to, and what it printed kept with its card (_python_end)."""
        code = str(args.get("code") or "")
        started = ToolSocket.main(self._python_start, agent, code) or {"error": f"{NAME} didn't answer: try again"}
        if started.get("error"):
            return started
        out = "(stopped)"
        try:
            out = run_isolated(code, started["tools"], ToolSocket.get().path, agent.token)
        finally:
            done = ToolSocket.main(self._python_end, agent, code, out)
        return done or {"text": out}

    def _python_start(self, agent, code):
        if (error := self._guard(agent)) is not None:
            return error
        tools = {t["name"]: list((t.get("inputSchema") or {}).get("properties") or {}) for t in self.all_tools()
                 if t["name"] not in SETUP_NAMES}
        try:
            check_code(code, tools)
        except ValueError as e:
            return {"error": str(e)}
        turn = agent.turn
        turn["nested"] = turn["at"] = self._item_for(turn, "run_python", {"code": code})  # (its calls: on its card)
        return {"tools": tools}

    def _python_end(self, agent, code, out):
        if (turn := agent.turn) is not None:
            turn["nested"] = None
            turn["code"].append({"code": code, "output": out})
            self._refresh_live(turn)
        return {"text": out}
