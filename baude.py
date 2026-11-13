#!/usr/bin/env python3
"""baude — a tiny GTK4 front-end for the `claude` CLI.

It spawns one long-lived `claude -p` process speaking the stream-json
protocol on stdin/stdout (the same protocol the Agent SDK uses) and renders
the events: streamed text, thinking, tool calls + results, permission
prompts, cost/usage. Everything "agentic" (tools, context, compaction,
MCP, hooks, CLAUDE.md...) is done by the CLI; baude is only a view.

    python3 baude.py [--cwd DIR] [--model MODEL] [--resume SESSION_ID]
    python3 baude.py --install | --uninstall   # app-menu launcher (per user)

Keys: Enter sends, Shift+Enter adds a line, Esc stops, Ctrl +/-/0 (⌘ too on macOS, or Ctrl+scroll)
zooms the text, Ctrl+V pastes images/files as attachments, Ctrl+N starts a new session, Ctrl+, (⌘,) opens Settings,
Ctrl+. (⌘.) Send Feedback over whatever's open, Ctrl+M (Control on macOS too) shows Meta, its box focused.
Images and PDFs can also be attached with the paperclip or dropped onto the window.

Set BAUDE_CLAUDE=/path/to/claude to use a specific claude binary (else claude on PATH, else, on macOS,
the Claude desktop app's).
"""
import argparse
import array
import base64
import collections
import contextlib
import glob
import hashlib
import html
import itertools
import json
import math
import os
import re
import shlex
import shutil
import socket
import struct
import weakref
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import queue
import urllib.parse
import uuid

import lookup
from macos import full_redraws, ns_string, objc_send  # (no GTK: used before it loads)
from baudelib.claude import (  # (Claude Code's process, without GTK: see baudelib/)
    _CLAUDE_VERSION, claude_env, claude_exe, claude_version, EFFORT_LABELS, HOST_SESSION_ENV, MACOS_DESKTOP_CLAUDE,
    MODELS, MODES, spent_since,
)
from baudelib import claude as claudelib  # (its ClaudeProc: Baude's is it, on the GTK main loop)
from baudelib.transcripts import (  # (Claude Code's transcripts, without GTK: see baudelib/)
    _CHAT_MODEL, _command_text, _first_text, _has_text, _INJECTED, _is_prompt, _KEY_BITS, _line_sum, _NOT_TYPED,
    _pack_keys, _prompt_texts, _RECORD_TIME, _RECORD_UUID, _scan_line, _scan_state, _TOOL_RESULT_REC, _unpack_keys,
    ChunkFill, ci_event, clean_prompt, find_session, history_item, history_items, HistoryIndex, projects_dir,
    read_history, read_session_info, record_key, scan_session, session_file, SESSION_SOURCES, task_notification,
    transcript_model,
)
import baudelib.client  # noqa: E402  (servers on the network and their sessions: see baudelib/client.py)
import baudelib.hosts  # noqa: E402  (this machine's host process, started when it's wanted: see baudelib/hosts.py)
from baudelib import models as modelslib  # noqa: E402  (what runs a model: claude, apple-claude or pi-claude)
from baudelib.models import (  # noqa: E402,F401
    APPLE_MODEL, apple_exe, LOCAL_MODELS, local_models, model_program, PI_CLAUDE, pi_exe,
)
from lookup import (  # (⌘K's engine: the names a folder has; see lookup.py)
    CATALOGUE_TTL, GITHUB, Names, PG_LISTS, REF_KINDS, REF_LOC_FIELDS, Ref, _CATALOGUE_TOKEN, _KUBE_ACTION, _KUBE_NAME,
    _git, _km, _ks, _kube_args, arango_eval, at_once, catalogue_hits, catalogue_ref, catalogue_shaped, db_index,
    db_names, find_spans, git_exe, github_data, kube_contexts, kube_fault, kube_fmt_age, kube_get, kube_local, kube_made_as,
    kube_run, kube_running, kube_seconds, kube_tool, kubeconfig_paths, pg_databases, pg_databases_cached, pg_query,
    pg_servers, pg_servers_cached, ref_date, ref_icon, ref_key, ref_palette_rank, ref_yaml_path,
    signal_glossary_exists, signal_place, signal_store,
)


def macos_fonts():
    """The fonts set up before Pango's loaded: text through FontConfig and FreeType on macOS, the code font Settings ->
    Code Font says, and the fonts bundled beside Baude (fontsetup)."""
    import fontsetup  # (no GTK: beside this file)
    fontsetup.setup()


def macos_app_name():
    """On macOS, the menu bar says Baude rather than Python: it names the app after its bundle (Python.app's),
    read when the app starts, so this renames the bundle in memory before GTK starts it (the CFBundleName of
    +[NSBundle mainBundle]'s info, as PyObjC apps do)."""
    import ctypes
    if sys.platform != "darwin":
        return
    try:
        bundle = objc_send(b"NSBundle", b"mainBundle")
        for which in (b"infoDictionary", b"localizedInfoDictionary"):
            if info := objc_send(bundle, which):
                objc_send(info, b"setObject:forKey:", ns_string("Baude"), ns_string("CFBundleName"),
                          types=(ctypes.c_void_p, ctypes.c_void_p))
    except Exception as e:
        print("baude: still named Python:", e, file=sys.stderr)


macos_fonts()
macos_app_name()
FULL_REDRAWS = full_redraws()

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, GObject, Graphene, Gsk, Gtk, Pango  # noqa: E402

import feedback  # noqa: E402,F401  (Menu → Send Feedback…: Record's too)
import fixview  # noqa: E402  (☰ → Ongoing Feedback: the fixes, approved or rejected)
import livefeed  # noqa: E402  (a chat followed as it's written: Record's too)
import pointers  # noqa: E402  (the pointer's shape: Record's too)
from livefeed import (  # noqa: E402
    LIVE_SECONDS, TURN_OPEN_SECONDS, external_state, read_appended, transcript_turn_open)
import update  # noqa: E402  (a new version from git, restarted onto once idle: Record's too)
import meta  # noqa: E402  (Meta, the helper in the side panel: Record's too; see meta.py)
from feedback import (  # noqa: E402,F401
    Capture, FEEDBACK_SAVE_KEYS, FeedbackDialog, FeedbackDraft, FeedbackHost, RegionEditor, RegionLayer, _visible_rect, capture_window, feedback_dir, feedback_items, save_feedback,
)
import widgets  # noqa: E402  (what Record draws as Baude does: the links' logos; see widgets.py)
from widgets import hover_uri, markup_links  # noqa: E402,F401  (a label's links, by where the pointer is)
from widgets import REF_ICON_SVG, RefIcon, draw_ref_icon, ref_icon_ops  # noqa: E402,F401
from widgets import HEADING_SCALE, Composer, ComposerMarkdown, ComposerView, fmt_tokens  # noqa: E402,F401
from widgets import ICON_SLOT, Disclosure, Spinner, Ticker, _card_status, _slot, _status_icon  # noqa: E402,F401
from widgets import (  # noqa: E402,F401
    _data_home, SCALE_MAX, SCALE_MIN, SETTINGS_KEY, settings_size, settings_switch, ZOOM_MODS, macos_appearance, scale_setting, zoom_keys,
    CSS, PALETTE_KEY, RefPaletteRow, TreeList, load_css, sidebar_action, ARTIFACT_TITLES, BAUDE_ICONS, BAUDE_SHAPES, BULLET, CODE_STYLES, CodeBlock, diff_block,
    EXISTS_FOR, FILE_SCHEME, FIND_RGB, Fade, FixedPicture, GUI_EDITORS,
    HOME_NAME, IMAGE_POOL, INDENT_STEP, INLINE_MEMO_BYTES, ImageActions, KUBE_ICONS,
    KUBE_KINDS, LineGutter, LinkLabel, MarkdownView, PictureWatch,
    REF_ABSENT_WHY, REF_SCHEME, SETTINGS_RENAMED, SUB_BULLET, TableScroll, WEB_IMAGE_MAX,
    WorkerPool, ZOOM_MAX, ZOOM_MIN, ZOOM_STEP, _ARTIFACT_URL, _BARE_URL,
    _BOLD, _BOLD_ITALIC, _CODE_SPAN, _FENCE, _HEADING, _IMAGE,
    _ITEM, _KEPT_LINK, _KUBE_PASCAL, _MD_LINK, _PATH, _QUOTE_MARK,
    _REF_DOTTED, _REF_EXT, _REF_ID, _REF_METHODS, _REF_MORE_EXT, _REF_PLAIN,
    _RULE, _STAR_ITALIC, _STRIKE, _TABLE_RULE, _UNDERSCORE_ITALIC, _dispose_tree,
    _emphasis, _exists_cache, _image_ext, _inline, _inline_markup, _inline_memo,
    _memo_bytes, _settings_path, _web_images, _web_waiting, bullet_for, code_spans,
    code_style, discard, editor_args, editor_command, ellipsize_dropdown, empty_label,
    ensure_icons, fetch_web_image, file_href, find_attrs, find_label, fit_size,
    fmt_scale, globe_icon, image_cache_path, image_dims, image_widget, link_markup,
    link_paths, load_settings, make_image_interactive, markdown_blocks,
    markdown_parse, markdown_table, on_label_link, open_code, open_in_editor, paned_grip,
    pretty_path, ref_code_markup, ref_command, ref_commit_range, ref_decision_markup, ref_epoch,
    ref_hashlike, ref_icon_slot, ref_kind, ref_kube, ref_kube_command, ref_link,
    ref_markup, ref_pr_info, ref_quiet, ref_span, ref_symbolish, relink_table,
    resolve_link, resolve_path, save_settings, scaled_texture, screen_scale, sel_label,
    set_markup_safe, themed_icon, time_ago, time_tip, toast, view_bar, walk,
)
from codeview import (  # noqa: E402,F401
    CODE_LIMIT, CodePage, CodeView, FILE_ICON_KINDS, FileIgnores, FileNode, FilesPage,
    file_children, file_icon, git_ignored, in_code, python_context,
)
from kubeview import (  # noqa: E402,F401
    CERT_MANAGER_CERTIFICATE, CERT_MANAGER_ISSUER, KEDA_SCALED_JOB, KEDA_SCALED_OBJECT, KEDA_TRIGGER_AUTH, KUBE_CLIMB,
    KUBE_CLUSTER_KINDS, KUBE_CONDITION_TONES, KUBE_LOG_LINES, KUBE_LOG_TAIL, KUBE_NODES, KUBE_PODS,
    KUBE_POOL, KUBE_REFRESH_S, KUBE_REF_FIELDS, KUBE_REF_SKIP, KUBE_RESTARTED_RECENTLY_S, KUBE_SECRETS_TEMPLATE,
    KUBE_SECTIONS, KUBE_SECTION_ICONS, K_AGE, K_NAME, K_NS, KubeLogTail,
    KubeRow, KubeSquares, KubeView, KubeWatch, _KUBE_CONDITION, _KUBE_JP_OPS,
    _KUBE_JSONPATHS, _KUBE_KLOG, _KUBE_MUST_HOLD, _KUBE_ORDER, _KUBE_PRINTED, _KUBE_PRINTERS,
    _age_col, _cpu_col, _endpoints, _ing_rules, _job_duration, _job_status,
    _kcount, _keys, _kp, _kp_condition, _kube_cell_text, _kube_jp_bracket,
    _kube_jp_close, _kube_jp_descend, _kube_jp_literal, _kube_jp_name, _kube_jp_test, _lb,
    _mem_col, _ncol, _node_ip, _node_ready, _node_roles, _node_usage,
    _owner, _pod_ready, _pod_usage, _slice_endpoints, _status_col, _subjects,
    _svc_ports, _svc_status, _true_conditions, kube_api_resources, kube_bytes, kube_col,
    kube_col_title, kube_condition_tone, kube_container_look, kube_containers, kube_controller, kube_cpu,
    kube_crd_page, kube_crd_printer, kube_custom_page, kube_delete_pod, kube_diagnose, kube_fmt_bytes,
    kube_jsonpath, kube_jsonpath_find, kube_list, kube_metrics_ctx, kube_node_metrics, kube_order,
    kube_page, kube_page_columns, kube_page_crd, kube_pod_metrics, kube_pod_status, kube_printer_cell,
    kube_printer_col, kube_printer_columns, kube_resource, kube_rows, kube_signin, kube_status_tone,
    kube_uid, kube_yaml_refs,
)
from schemaview import (  # noqa: E402,F401
    ARANGO_SCHEMA_JS, DB_POOL, PG_CACHE, PG_SCHEMA_SQL,
    PG_SECRET, SchemaCanvas, SchemaView, _FORGOTTEN, _LOCATING, _LOCATING_LOCK,
    _arango_snake, arango_guess_ends, arango_normalize, arango_rows, arango_schema, db_find,
    db_indexed, db_label, db_locate, pg_mask,
    pg_normalize, pg_rows, pg_schema, schema_layout,
)
from gitview import (  # noqa: E402,F401
    GH_POOL, GIT_CARDS, GIT_CARD_FRESH, GIT_CHAT, GIT_FIND_LIMIT, GIT_LANES,
    GIT_LOG_LIMIT, GIT_MERGE_LIMIT, GIT_POOL, GitDelta,
    GitLane, GitPage, _CHAT_GIT, _GH_STATE, _GIT_COMMIT_OUT, _GIT_FMT,
    _GIT_PUSH_OUT, _gh_json, chat_git_events, gh_state, git_ahead_behind, git_chat_commit,
    git_checkouts, git_commit_card, git_delta_markup, git_find, git_full_when,
    git_lanes, git_log, git_numstat, git_parent_branch, github_login,
    github_repo,
)

APP_ID = "dev.baude.Baude"


def _process_age():
    """Seconds since this process was started (interpreter start-up included), from /proc."""
    try:
        with open("/proc/self/stat") as f:
            start_ticks = int(f.read().rsplit(")", 1)[1].split()[19])
        with open("/proc/uptime") as f:
            uptime = float(f.read().split()[0])
        return uptime - start_ticks / os.sysconf("SC_CLK_TCK")
    except (OSError, ValueError, IndexError):
        return 0.0


TRACE = os.environ.get("BAUDE_TRACE")
_TRACE_BASE = time.monotonic() - _process_age()  # monotonic time of the process's start
_traced = set()


def trace(what, once=True):
    """BAUDE_TRACE=1: when start-up milestones happen, in ms since the process started."""
    if TRACE and not (once and what in _traced):
        _traced.add(what)
        cpu = time.process_time() * 1000  # (CPU used so far: unlike wall time, not inflated by a busy machine)
        print(f"baude-trace {(time.monotonic() - _TRACE_BASE) * 1000:7.0f} ms {cpu:7.0f} cpu  {what}",
              file=sys.stderr, flush=True)
        if TRACE == "exit" and {"sidebar filled", "first frame", "claude ready"} <= _traced:
            GLib.idle_add(lambda: Gio.Application.get_default().quit())


trace("imported")
# (MODELS, the fallback model list: baudelib.claude)
# (APPLE_MODEL, apple_exe, PI_CLAUDE, LOCAL_MODELS, local_models and model_program: baudelib.models)


# (MODES and EFFORT_LABELS: baudelib.claude)


# The app's own sizes (Settings → Size), the same in every window and pane: the text's and the icons'. 1.0 is the usual.


def restart_state_path():
    return os.path.join(os.path.dirname(_settings_path()), "restart.json")


def take_restart_state(path):
    """The state a restarting baude left for this one (read once, then removed): {windows: [each window's
    restart_state], front, updated} (BaudeApp.restart), or one window's from before. A window's own, already read: as
    it is."""
    if isinstance(path, dict):
        return path
    if not path:
        return None
    try:
        with open(path) as f:
            state = json.load(f)
    except (OSError, ValueError):
        return None
    try:
        os.remove(path)
    except OSError:
        pass
    return state if isinstance(state, dict) else None


def record_item():
    """The menu's item for Record (record.py)."""
    return "Open in Record…"


# the display's font resolution as it was (None: not changed), the icons' CSS with what it holds, and the sizes for
# what Baude draws itself: the text's, and an icon's in px (16 at 1.0)
_scales = widgets.scales  # (one dict: widgets' RefIcon is the icons' size too)
_scales.update({"dpi": None, "icons": None, "icons_css": "", "text": 1.0, "icon_px": 16})


def apply_scales(display):
    """widgets.apply_scales (Settings → Size), Baude's spinners resized with the logos."""
    widgets.apply_scales(display, Ticker.spinning)


REF_TOOL_KINDS = ("tool", "subagent", "skill")  # (looked up in memory, never kept from an earlier run)
# what Signal looks up while it isn't running (look_locally: no network, no cluster but the local ones the catalogue
# reads, nothing kept), and what it looks
# for on other branches when this checkout hasn't it (_on_branches), in the REF_BRANCHES most recently changed too
REF_LOCAL_KINDS = ("file", "branch", "env", "constant", "symbol", "config", "service", "k8s", "package", "cli",
                   "external", "route", "tool", "subagent", "skill")
REF_BRANCH_KINDS = ("symbol", "file")
REF_BRANCHES = 12
REF_SENSITIVE = re.compile(r"(^|/)(\.env[^/]*|[^/]*\.env|[^/]*secret[^/]*|[^/]*\.(pem|key|p12|pfx))$", re.I)
_REF_BLOCK_ROUTE = re.compile(rf"(?<![\w-])(?:{_REF_METHODS})\s+(?:…|\.\.\.)?/[\w./{{}}<>:~*-]*[\w}}>*-]")
REF_DECISION_FIELDS = ("message", "surface", "verdict", "kind", "path", "line", "role", "note", "why", "by", "at")


def ref_candidates(text, skipped=None, hints=None):
    """The names in a reply worth looking up: {surface: (kind guess, shown before it's found)}. Backticked
    spans can be anything name-shaped, or a span ref_span reads (a piece of code is shown only once found);
    plain text only the shapes that are rarely ordinary words. Code blocks and paths that already link (they
    exist relative to the folder) are left out. A name the reply calls a Kubernetes object or a context ("the `x`
    Deployment") is one, whatever its shape, and hints gets the kind it was given ({surface: kind})."""
    found = {}
    blocks = re.compile(r"(?ms)^\s*```.*?(?:^\s*```[^\n]*$|\Z)")
    for block in blocks.findall(text):  # (a route in a diagram or a curl line: shown once it's found)
        for m in _REF_BLOCK_ROUTE.finditer(block):
            found.setdefault(ref_key(m.group(0)), ("route", False))
    text = blocks.sub("", text)
    parts = re.split(r"(`[^`\n]+`)", text)
    for i, part in enumerate(parts):
        if part.startswith("`") and part.endswith("`") and len(part) > 2:
            raw = part[1:-1]
            before, after = parts[i - 1] if i else "", parts[i + 1] if i + 1 < len(parts) else ""
            if tool := ref_tool_kind(raw, before, after):
                found[raw] = (tool, True)  # a tool, a subagent type or a skill Claude Code has
            elif (hint := ref_kube_hint(before, after)) and re.fullmatch(_KUBE_NAME, raw) and not resolve_path(raw):
                found[raw] = ("k8s", hint != "context")  # (a context may be any word's: shown once it's found)
                if hints is not None:
                    hints[raw] = hint
            elif (kind := ref_kind(raw)) and not resolve_path(raw):
                found[ref_key(raw)] = (kind, not ref_quiet(raw))
            elif re.fullmatch(r"\d{7,12}", raw):  # (a hash that's all digits, or a number: linked only if git has it)
                found[raw] = ("commit", False)
            elif skipped is not None and not kind and re.fullmatch(r"[^\s]{2,80}", raw) and not resolve_path(raw):
                skipped[raw] = skipped.get(raw, 0) + 1  # a name, but not a kind Signal knows yet
            continue
        part = re.sub(r"\[[^\]]*\]\([^)\s]*\)|https?://\S+", " ", part)  # links are links already
        for m in re.finditer(r"\b([A-Z][A-Za-z]+|mcp__[\w-]+) tool\b", part):  # "the Agent tool"
            if m.group(1) in TOOL_NAMES:
                found.setdefault(m.group(0), ("tool", True))
        for m in _REF_PLAIN.finditer(part):
            s, kind = m.group(0), m.lastgroup
            if kind == "commit" and not ref_hashlike(s):
                continue
            if kind == "file" and resolve_path(s):
                continue
            found.setdefault(ref_key(s), (kind, False))
    return found


def ref_tool_kind(name, before="", after=""):
    """Whether a backticked name is a tool Claude Code has (or a subagent type, or a skill): "tool", "subagent",
    "skill" or None. A tool name that's also a plain word (Read, Write, Agent, Skill) counts only with "tool"
    next to it; elsewhere it's left to the model pass, which reads the context."""
    if name in TOOL_NAMES:
        if re.fullmatch(r"[A-Z][a-z]+", name):
            return "tool" if re.search(r"\btools?\b", (before[-40:] + " " + after[:40]).lower()) else None
        return "tool"
    if name in SUBAGENTS and (not SUBAGENTS[name].get("built_in") or "-" in name):
        return "subagent"
    if name in SKILLS and ("-" in name or ":" in name):
        return "skill"
    return None


def ref_codeish(surface):
    """Whether a name is written as code (a path, a route, a flag or command, an env var, a dotted, snake_case or
    camelCase name): never plain English, wherever it's written."""
    kind = ref_kind(surface)
    return kind in ("file", "route", "env", "cli", "commit", "pr") or kind == "symbol" and ref_symbolish(ref_key(surface)) \
        or (kube := ref_kube(surface)) is not None and kube[0] != "name"


def ref_redact(line):
    """A line from a file that may hold secrets, with every value hidden: NAME=•••."""
    return re.sub(r"([=:]\s*)\S.*", r"\1•••", line)


def blob_sha(data):
    """git's hash of a file's bytes (what `git hash-object` prints), without running git."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def ref_map_line(old, new, line):
    """line (1-based) of the old lines, carried into the new ones through a diff; None if that line
    itself changed."""
    if max(len(old), len(new)) > 20000:
        return None
    import difflib
    i = line - 1
    for tag, i1, i2, j1, _j2 in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if i1 <= i < i2:
            return j1 + (i - i1) + 1 if tag == "equal" else None
    return None


def ref_find_snippet(loc, new, sensitive=False):
    """Where a location's line is in the new lines, found by the lines stored around it: the same line
    with the most matching neighbours (then the nearest), else a close match within 200 lines."""
    snippet = (loc.get("snippet") or "").split("\n")
    start, line = loc.get("snippet_start"), loc.get("line")
    if not start or not line or not (0 <= line - start < len(snippet)):
        return None
    k = line - start
    norm = ref_redact if sensitive else (lambda s: s)

    def around(j):
        return sum(1 for d in range(-k, len(snippet) - k)
                   if 0 <= j + d < len(new) and norm(new[j + d]) == snippet[k + d])
    if snippet[k].strip():
        exact = [j for j, s in enumerate(new) if norm(s) == snippet[k]]
        if exact:
            return min(exact, key=lambda j: (-around(j), abs(j + 1 - line))) + 1
    import difflib
    want, best, at = "\n".join(snippet), 0.8, None
    for j in range(max(0, line - 201), min(len(new), line + 199)):
        window = "\n".join(norm(s) for s in new[max(0, j - k):j - k + len(snippet)])
        m = difflib.SequenceMatcher(None, want, window)
        if m.quick_ratio() >= best and (r := m.ratio()) >= best:
            best, at = r, j
    return at + 1 if at is not None else None


def ref_in_order(want, have):
    """Whether the names in want all appear in have, in that order (others may come between)."""
    it = iter(have)
    return all(any(w == h for h in it) for w in want)


def ref_route_score(template, target):
    """How well a route template (/jobs/{job_id}) matches a path as written (/jobs/abc, /jobs/{id}):
    None if it doesn't, else the number of segments that match literally. A catch-all ({rest:path})
    scores below every route that matches without one."""
    t, g = template.strip("/").split("/"), target.strip("/").split("/")
    param = re.compile(r"\{[^}]*\}|<[^>]*>|:\w+|\*")
    score = 0
    for i, a in enumerate(t):
        if re.fullmatch(r"\{\w+:path\}", a):
            return score - 1000
        if i >= len(g) or not (param.fullmatch(a) or param.fullmatch(g[i]) or a == g[i]):
            return None
        score += a == g[i] and not param.fullmatch(a)  # (a parameter named the same isn't a literal match)
    return score if len(t) == len(g) else None


def ref_name_role(path, text, name):
    """What a line does with an UPPER_SNAKE name: sets it (an env var), reads it from the environment,
    is its secret source, defines it (a constant), uses it, or is documentation."""
    base, n = os.path.basename(path).lower(), re.escape(name)
    if "doppler" in path.lower():
        return "secret source"
    if (base.startswith(".env") or base.endswith(".env")) and re.match(rf"\s*(export\s+)?{n}\s*=", text):
        return "set"
    if base.endswith((".yaml", ".yml")) and (re.match(rf"\s*-?\s*[\"']?{n}[\"']?\s*[:=]", text)
                                             or re.match(rf"\s*-?\s*name:\s*[\"']?{n}[\"']?\s*(#.*)?$", text)):
        return "set"
    if (base.startswith("dockerfile") or base.endswith((".dockerfile", ".containerfile"))) \
            and re.match(rf"\s*(ENV|ARG)\s+{n}\b", text):
        return "set"
    if base.endswith((".sh", ".bash", ".zsh", ".envrc")) and re.match(rf"\s*(export\s+)?{n}=", text):
        return "set"
    if re.search(rf"environ(?:\.get)?\s*[\[(]\s*[\"']{n}[\"']|getenv\(\s*[\"']{n}[\"']|env::var\(\s*\"{n}\""
                 rf"|process\.env(?:\.{n}\b|\[\s*[\"']{n}[\"'])|import\.meta\.env\.{n}\b"
                 rf"|(?:validation_)?alias\s*=\s*[\"']{n}[\"']", text):
        return "read"
    code = base.endswith((".py", ".pyi", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".rs", ".go"))
    if not code and re.search(rf"\$\{{?{n}\b", text):
        return "read"  # ${NAME} in a shell script, a compose file, a workflow
    if code and re.match(rf"\s*(?:export\s+)?(?:pub\s+)?(?:(?:const|let|var|static|final)\s+)?{n}\s*(?::[^=]*)?=(?!=)",
                         text):
        return "defined"
    return "doc" if base.endswith((".md", ".rst", ".txt")) else "used"


REF_CODE_PATHS = ["*.py", "*.pyi", "*.ts", "*.tsx", "*.js", "*.jsx", "*.mjs", "*.cjs", "*.rs", "*.go", "*.sql"]


def ref_is_test(path):
    base = os.path.basename(path)
    return any(d in "/" + path for d in ("/tests/", "/test/", "/__tests__/")) or base.startswith("test_") or \
        ".test." in base or ".spec." in base


def ref_def_role(path, text, name):
    """What kind of definition of name a line is (None if it isn't one)."""
    n = re.escape(name)
    if re.search(rf"(create_table\(|__tablename__\s*=|CREATE\s+TABLE\s+(IF NOT EXISTS\s+)?)\s*[\"']?{n}\b", text, re.I):
        return "table"
    if re.search(rf"(Column|ForeignKey)\(\s*[\"']{n}[\"']", text):
        return "column"
    if re.match(rf"\s*revision\s*(:[^=]*)?=\s*[\"']{n}[\"']", text):
        return "migration"  # alembic: revision = "relay_0039"
    if re.match(rf"\s*(async\s+)?def\s+{n}\s*\(", text):
        return "method" if text[:1].isspace() else "function"
    if re.match(rf"\s*class\s+{n}\b", text) or re.search(rf"\b(class|struct|trait)\s+{n}\b", text):
        return "class"
    if re.search(rf"\b(interface|type|enum)\s+{n}\b", text):
        return "type"
    if re.search(rf"\b(function|fn|func)\s+(\([^)]*\)\s*)?{n}\b", text):
        return "function"
    if re.match(rf"(export\s+)?(const|let|var)\s+{n}\b", text) or re.match(rf"{n}\s*(:[^=]*)?=[^=]", text):
        return "variable"  # top-level only: a local variable inside a function isn't a definition worth a link
    if re.search(rf"\bexport\s*(type\s*)?\{{[^}}]*\bas\s+{n}\b", text):
        return "re-export"
    if re.search(rf"self\.{n}\s*(:[^=]*)?=[^=]", text):
        return "attribute"
    if re.match(rf"\s+(async\s+)?(static\s+)?([gs]et\s+)?{n}\s*(<[^>]*>)?\([^)]*\)[^=;{{]*\{{", text):
        return "method"  # (a TypeScript method, its body on that line or below)
    if re.match(rf"\s+(readonly\s+|public\s+|private\s+)?{n}\??\s*:", text) and not path.endswith(".sql"):
        return "field"
    return None


def ref_member_re(name):
    """A line that declares name as a member of something: a field or parameter (name: str, name?: T), a dict or
    object key ("name": ..., name: ...), a column (Column("name", ...)), a model's attribute (name = Column(...))
    or a YAML key."""
    n = re.escape(name)
    return re.compile(rf"(?:^|[\s{{(,;])(?:readonly\s+)?{n}\??\s*:(?![:=])|[\"']{n}[\"']\s*:|Column\(\s*[\"']{n}[\"']"
                      rf"|^\s+{n}\s*(?::[^=]*)?=\s*[\w.]*(?:Column|Field|column|field)\(")


_REF_ANY_MEMBER = re.compile(r"^\s*(?:readonly\s+)?[\"']?[A-Za-z_]\w*[\"']?\??\s*:(?![:=])|^\s*(?:\w+\.)?Column\("
                             r"|^\s+\w+\s*(?::[^=]*)?=\s*[\w.]*(?:Column|Field|column|field)\(")


_REF_SIGNATURE = re.compile(r"\s*(?:(?:export|async|static|public|private|protected|pub)\s+)*(?:def|function|fn|func|class)\b"
                            r"|\s*(?!(?:\w+\.)?Column\()[\w.]+\s*(?:<[^>]*>)?\(")  # (whose parameters aren't the block's members)


def ref_shape_blocks(lines, names):
    """Where a file's lines declare every one of names as members of one thing (a class, an interface or type, a
    table, a YAML mapping, an object or dict, a parameter list): [(the 0-based line that opens it, how many other
    members it has)]. It opens on the line above the members that's indented less, or on their line when
    they're all on one."""
    decls, leads = {}, {}  # lines declaring each name (a one-line object), and those that aren't a signature (a block's)
    for n in names:
        pat = ref_member_re(n)
        decls[n] = {i for i, s in enumerate(lines) if n in s and pat.search(s)}
        leads[n] = {i for i in decls[n] if not _REF_SIGNATURE.match(lines[i])}
        if not decls[n]:
            return []
    indent = lambda s: len(s) - len(s.lstrip())  # noqa: E731
    out, rare = {}, min(names, key=lambda n: len(decls[n]))
    for i in sorted(decls[rare])[:200]:
        if all(i in decls[n] for n in names):
            h, extra = i, len(re.findall(r"[\"']?\b[A-Za-z_]\w*[\"']?\??\s*:(?![:=])", lines[i])) - len(names)
        elif i in leads[rare]:
            ind, h, e = indent(lines[i]), i - 1, i + 1
            while h >= 0 and (not lines[h].strip() or indent(lines[h]) >= ind):
                h -= 1
            while e < len(lines) and (not lines[e].strip() or indent(lines[e]) >= ind):
                e += 1
            if h < 0 or not all(h in decls[n] or any(h < j < e and indent(lines[j]) == ind for j in leads[n])
                                for n in names):
                continue
            extra = sum(1 for j in range(h + 1, e) if indent(lines[j]) == ind and _REF_ANY_MEMBER.match(lines[j])) - \
                sum(1 for n in names if h not in decls[n])
        else:
            continue
        out.setdefault(h, max(0, extra))
    return list(out.items())


def ref_container(lines, h, path):
    """What opens on line h (0-based) of a file, holding members: (role, name). The role is class, type, table,
    entity (a data model's fields in YAML), parameters or object."""
    s = lines[h]
    if m := re.match(r"\s*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+(\w+)", s):
        return "class", m.group(1)
    if m := re.search(r"\b(?:interface|type|struct|enum)\s+(\w+)", s):
        return "type", m.group(1)
    if re.search(r"\b(?:Table|create_table)\(", s):
        m = re.search(r"(?:Table|create_table)\(\s*[\"'](\w+)", s) or \
            (h + 1 < len(lines) and re.match(r"\s*[\"'](\w+)[\"']", lines[h + 1]))
        return "table", m.group(1) if m else ""
    if path.endswith((".yaml", ".yml")) and re.match(r"\s*fields\s*:\s*$", s):
        ind = len(s) - len(s.lstrip())
        return "entity", next((m.group(1) for t in reversed(lines[:h]) if len(t) - len(t.lstrip()) < ind
                               and (m := re.match(r"\s*-\s*name:\s*[\"']?([\w.-]+)", t))), "")
    if m := re.search(r"\b(?:def|function|fn|func)\s+(\w+)", s):
        return "parameters", m.group(1)
    m = re.search(r"(\w+)[\"']?\??\s*[:=]\s*(?:[\w.\[\]]+\s*)?[{(\[]", s) or re.match(r"\s*-?\s*([\w.-]+)\s*:\s*$", s)
    return "object", m.group(1) if m else ""


_REF_TYPING = {"Literal", "None", "Optional", "Union", "Field", "Any", "List", "Dict", "Set", "Tuple", "Annotated",
               "Mapped", "Column", "True", "False", "ClassVar", "Final", "Sequence", "Mapping", "Callable", "Iterable",
               "Record", "Partial", "Required", "Readonly", "Array", "Promise", "Pick", "Omit", "Type", "String",
               "Integer", "Boolean", "Text", "JSON", "DateTime", "BaseModel", "Self", "Path", "Exception"}


def ref_excerpt_re(text):
    """A piece of code as a pattern: any spacing where it has spaces (and none), either quote for a quote, and
    anything for an ellipsis (create_app(...).openapi()). Not inside a longer name."""
    out = []
    for tok in re.split(r"(\s+|\.\.\.|…)", text):
        if tok.isspace():
            out.append(r"\s*")
        elif tok in ("...", "…"):
            out.append(r".*?")
        elif tok:
            out.append("".join("[\"'`]" if c in "\"'" else re.escape(c) for c in tok))
    return re.compile((r"(?<![\w.])" if re.match(r"\w", text) else "") + "".join(out) +
                      (r"(?!\w)" if re.search(r"\w$", text) else ""))


class Signal(Names):
    """One folder's glossary (<folder>/.baude/signal.db) and the thread that fills it. The main loop
    owns `refs` (surface -> Ref) and reads it while drawing; the worker thread owns the database, git and gh."""

    _instances = {}
    GITHUB_TTL = 600              # PR states change: GitHub answers are kept this long
    STILL_S = 0.4                 # how long the chats are still before the workers go on (wait_still)
    moved_at = 0.0                # when a chat last moved, any window's (time.monotonic(); main loop: moved)

    @classmethod
    def moved(cls):
        """A chat moved: scrolled, gliding, its scrollbar dragged (its view's value changed; main loop)."""
        cls.moved_at = time.monotonic()

    def wait_still(self):
        """(Worker.) Hold on while a chat moves, and STILL_S after (each git it starts too: git_wait). A lookup's own
        Python and its gits kept the window waiting for Python's lock: a long chat flung up as it opened drew 14
        frames a second, 39 with Signal off."""
        while (left := Signal.moved_at + self.STILL_S - time.monotonic()) > 0:
            time.sleep(left)

    @classmethod
    def for_folder(cls, folder, home=None, halted=False):
        """The Signal that searches folder: one for each package of each checkout of a repository (signal_place),
        whichever of its folders a chat is in, its glossary the repository's. home: the folder the chat started in
        (a worktree's chat: its main checkout), whose glossary from before is folded in. halted: one made now starts
        off (only its catalogue wanted, for the glossary's search: nothing is looked up, and no glossary is made
        where there's none)."""
        folder = os.path.realpath(folder)
        home = os.path.realpath(home or folder)
        key = signal_place(folder).key
        if key not in cls._instances:
            cls._instances[key] = cls(folder, home, halted)
        else:
            cls._instances[key].adopt(folder, home)
        return cls._instances[key]

    def adopt(self, folder, home):
        """Another folder of this Signal's package (another chat's): its glossary from before is folded in too."""
        old = signal_store(folder, home)
        if old not in self.olds and signal_glossary_exists(folder, home) and \
                os.path.realpath(old) != os.path.realpath(self.dir):
            self.olds.add(old)
            self.jobs.put(("load", None, None))  # (_open folds it in, then the names are read again)

    def __init__(self, folder, home=None, halted=False):
        Names.__init__(self, folder, home)
        self.refs, self.strong, self.listeners = {}, set(), []
        self.jobs = queue.SimpleQueue()
        # what it's doing, for the Signal page: now (None: idle), a log, replies waiting and read, and the
        # backticked names it doesn't look up (not a kind it knows yet)
        self.activity, self.log, self.waiting, self.read = None, [], 0, 0
        self.skipped, self.status_listeners = {}, []
        self.reader_chat = []  # what the reader said and did, for the Signal page: [(when, kind, text)] (reader_said)
        self.phrases, self.phrase_re = set(), None  # names Sonnet found in plain prose ("the Relay"), linked there too
        # the name catalogue (catalogue_hits; made in the worker, swapped in whole here), when it was made, and
        # whether one is on its way
        self.catalogue, self.catalogue_at, self.catalogue_busy = {}, 0.0, False
        self.shown_texts = {}  # (the replies drawn while it isn't running: linked again when the catalogue is in)
        self.agent = SignalReader(self) if load_settings().get("signal_reader", True) else None  # the reader
        self.transcript = None  # the chat it reads, and what's in it (main loop; follow)
        self._chat, self._checkouts, self._evidence_soon = None, None, None  # (_chat: a SignalChat)
        self._chats = {}  # (the chats read here lately, by transcript, the last followed last: follow)
        self._evidence_from = None  # (the first reply whose evidence may have changed since it went to the worker)
        self.chat_events, self.chat_texts, self.chat_replies, self.chat_evidence = [], [], [], []
        # per reply: what a name in it points at ((message, surface) -> decision; main loop), and the replies
        # the reader has decided
        self.decisions, self.decided_upto, self._phrase_res = {}, set(), {}
        self._by_surface, self._order, self._order_for = {}, {}, None  # (decisions by name; the replies' order)
        self._looked, self._sock_path, self._schemas = set(), None, {}  # (worker / the tools' socket)
        self.tools_said, self.question_listeners = set(), []  # (tool schemas: fetched / asked; who asks you)
        self.texts = {}  # the replies read (hash -> text, session, written_at): looked at again when tools are known
        self.halted = halted  # turned off (stop): the worker drops what it's doing
        # the names looked up here alone while it isn't running (look_locally), and the branches the chat names, in the
        # order it named them (where a name this checkout hasn't may be: _branch_revs)
        self._local_asked, self.chat_branches = set(), {}
        self.runners = set()  # the windows whose chat has it activated: it stops when none has (main loop)
        # the language servers (RefCode, worker thread), what's been said about them, and who asks you to fetch them
        self.code, self.lsp_said, self.ask_listeners = None, set(), []
        self.github = github_repo(self.top) if self.top else None
        self._repos = {}  # the other repositories each chat worked in (chat_repos; worker thread)
        self._git_asked = set()  # names looked up in git while it isn't running (main loop)
        threading.Thread(target=self._run, daemon=True, name="baude-signal").start()
        self.jobs.put(("load", None, None))
        self.jobs.put(("tools", None, None))

    # ---- main loop side
    def queue(self, text, session=None, written_at=None, touched=(), look=True):
        """Look up the names in a reply. Returns their surfaces; new ones show as pending at once. With look False (a
        chat that doesn't have Signal activated, in a window beside one that does), only what's known is drawn."""
        hints = {}  # (the kind a reply gives a name: "the `x` Deployment")
        cands = ref_candidates(text or "", self.skipped, hints)
        for s, c in catalogue_hits(text, self.catalogue, hints).items():  # (and the names known to be there)
            cands.setdefault(s, c)
        message = hashlib.sha1((text or "").encode("utf-8", "replace")).hexdigest()[:16]
        if text and text.strip() and look and not self.halted:  # (looked at again when tools are known)
            self.texts[message] = (text, session, written_at)
            self.catalogue_soon()
        if not cands:
            self.status_changed()
            return []
        for s, (kind, _strong) in cands.items():  # (a branch it names: where a name this checkout hasn't may be)
            if kind == "branch" or any((self.catalogue.get(b) or {}).get("kind") == "branch"
                                       for b in (s, "origin/" + s)):
                self.chat_branches.pop(s, None)
                self.chat_branches[s] = True
        if self.halted or not look:  # (not running: what the glossary knows is drawn, and nothing new is kept;
            # a name the catalogue has is linked from what it says, the catalogue made if it's old, and the rest are
            # looked up here alone: look_locally)
            self.strong.update(s for s, (_kind, strong) in cands.items() if strong)
            if text and text.strip():
                self.shown_texts[message] = text
                while len(self.shown_texts) > 2000:
                    self.shown_texts.pop(next(iter(self.shown_texts)))
                self.catalogue_soon()
            self.link_catalogued(cands)
            self.look_in_git(cands)
            self.look_locally(cands, touched, hints)
            self.status_changed()
            return list(cands)
        new = []
        for s, (kind, strong) in cands.items():
            if strong:
                self.strong.add(s)
            if s not in self.refs or self.refs[s].data.get("catalogue") or self.refs[s].data.get("local"):
                # (linked from the catalogue, or looked up while it wasn't running: looked up now, and kept)
                self.refs[s] = Ref(s, kind)
                new.append(s)
        meta = {"session": session, "written_at": written_at, "touched": list(touched), "message": message,
                "hints": hints}
        self.jobs.put(("resolve", cands, meta))
        self.waiting += 1
        if new:
            self.notify(new)
        self.status_changed()
        return list(cands)

    def github_changed(self):
        """GitHub's lists are new: the pull requests and issues named here say where they stand now (nothing asked for:
        it's what the lists say), as a closed issue's # goes grey and a merged pull request purple. The labels showing
        one that's changed are drawn again."""
        changed = []
        for ref in list(self.refs.values()):
            listed = GITHUB.known(ref.data.get("repo"), ref.data.get("number")) if ref.kind == "pr" else None
            data = github_data(listed) if listed is not None else {}
            if any(ref.data.get(k) != v for k, v in data.items()):
                ref.data.update(data)
                changed.append(ref.surface)
        if changed:
            self.notify(changed)

    def link_catalogued(self, names):
        """Link the names the catalogue has that the glossary doesn't (Signal not running): from what the catalogue
        says (catalogue_ref). The labels showing them are drawn again."""
        new = []
        for s in names:
            if s not in self.refs and (entry := self.catalogue.get(s)) is not None:
                self.refs[s] = catalogue_ref(s, entry)
                new.append(s)
        if new:
            self.notify(new)

    def look_locally(self, cands, touched=(), hints=None):
        """Look up the names in a reply Signal isn't running for (Show links alone), as it would but with nothing but
        what's here: the repository's files, a search of its code, the language servers if LSP is on and they're
        installed, the branches the chat names (_on_branches). No model, no network, nothing kept: one found is
        linked for now, and looked up again (and kept) once Signal runs."""
        names = {s: kind for s, (kind, _strong) in cands.items() if kind in REF_LOCAL_KINDS and s not in self.refs
                 and s not in self._local_asked}
        if names and load_settings().get("signal_local", True):
            self._local_asked.update(names)
            self.jobs.put(("local", names, {"touched": list(touched), "hints": dict(hints or {})}))

    def look_in_git(self, cands):
        """Look up the commits and pull requests that names written in replies point at, in git alone (Signal not
        running: no model and no network, nothing kept). One found is linked; one not found stays plain."""
        names = {s: kind for s, (kind, _strong) in cands.items()
                 if kind in ("commit", "pr") and s not in self.refs and s not in self._git_asked}
        if names:
            self._git_asked.update(names)
            self.jobs.put(("git", names, None))

    FOLLOW_MS = 8  # how long a chat just opened is read at a time, between frames (_follow_some)
    CHATS = 4  # the chats kept read (_chats): windows of one repository share its Signal, each following its own

    def follow(self, transcript):
        """The chat shown (its transcript): what's new in it is read (what Claude saw, as evidence), and the reader
        reads it, from the start or on from where it was (main loop). Not running, it's only read, so the reader's
        decisions carry from reply to reply as they're drawn. This runs for each line another program appends to the
        chat: only what was appended is read (SignalChat). A chat just opened is read FOLLOW_MS at a time between
        frames, once it's still (_follow_some), to the same lists: read at once as it opened, an 85 MB chat and its
        subagents' (96 MB more) held its first frame up 0.9 s. The last few chats followed are kept (CHATS), each
        read on from where it was: windows sharing this Signal each follow theirs as it grows, and one being read
        isn't started again when another's grows."""
        if transcript:
            self.transcript = transcript
        if not self.transcript or not os.path.exists(self.transcript):
            return
        if self._chat is None or self._chat.path != self.transcript:
            chat = self._chats.pop(self.transcript, None)
            if chat is None:
                chat = SignalChat(self.transcript, self.root, self.other_checkouts())
                chat.behind = True
                GLib.idle_add(self._follow_some, chat, None, priority=GLib.PRIORITY_LOW)
            self._chats[self.transcript] = chat
            while len(self._chats) > self.CHATS:
                self._chats.pop(next(iter(self._chats)))  # (its reading stops: _follow_some)
            self._chat, self._evidence_from = chat, None
            if chat.behind:  # (another chat's lists go: this one's as far as it's read, all of them once it is)
                self._chat_read(chat, None, True)
            else:  # (read before: on from there, its evidence looked at again from the start, as if read afresh)
                chat.read()
                self._chat_read(chat, 1, True)
        elif not self._chat.behind:  # (else what's new is read with the rest)
            self._chat_read(self._chat, self._chat.read())

    def _follow_some(self, chat, first):
        """FOLLOW_MS of reading a chat just opened (follow), then on in the next idle moment; or on once the chats
        have been still a moment (STILL_S). first: the first reply its reads so far say changed. Once it's all read,
        the replies drawn meanwhile are linked again where the reader's decisions say: a name means what the nearest
        reply it decided says (decision), and which is nearest wasn't known. A chat another window's has taken the place
        of goes on being read (it's kept: _chats), and shows what's read of it when it's followed again."""
        if self._chats.get(chat.path) is not chat:
            return False  # (not kept any more: other chats since, or Signal's read them afresh)
        if (wait := Signal.moved_at + self.STILL_S - time.monotonic()) > 0:
            GLib.timeout_add(int(wait * 1000) + 1, self._follow_some, chat, first, priority=GLib.PRIORITY_LOW)
            return False
        got = chat.read(time.monotonic() + self.FOLLOW_MS / 1000)
        first = got if first is None else first if got is None else min(first, got)
        if chat.behind:
            GLib.idle_add(self._follow_some, chat, first, priority=GLib.PRIORITY_LOW)
            return False
        if chat is not self._chat:
            return False  # (read: its lists go out when it's followed again)
        self._chat_read(chat, first, True)
        self._phrase_res.clear()
        if carried := [s for s, ds in self._by_surface.items() if any(d["by"] == "reader" for _k, d in ds)]:
            self.notify(carried)
        return False

    def _chat_read(self, chat, first, new=False):
        """What a read of the chat found (first: SignalChat.read's, None if nothing's new; new: it's just been read,
        or is about to be): its lists, the evidence looked at again from first, and the reader on."""
        if first is not None or new:  # (another chat's lists go, even for one with nothing in it yet)
            first = first or len(chat.replies) + 1
            self.chat_events, self.chat_texts, self.chat_replies, self.chat_evidence = \
                chat.events, chat.texts, chat.replies, chat.evidence
            if first <= len(chat.replies):  # (a reply that's new, or after new evidence: the worker looks again)
                self._evidence_from = min(first, self._evidence_from or first)
                if self._evidence_soon is None and not self.halted:
                    self._evidence_soon = GLib.timeout_add(300, self._evidence_when_ready)
        if self.agent is not None and not self.halted:
            self.agent.follow(self.transcript)

    def forget_chat(self):
        """No chat to read (a new one, with no transcript yet): the reader stops reading the one before (main loop)."""
        self.transcript, self._chat, self._evidence_from = None, None, None
        self.chat_events, self.chat_texts, self.chat_replies, self.chat_evidence = [], [], [], []
        if self.agent is not None:
            self.agent.restart_chat(None)

    def steer(self, transcript):
        """The chat a window that runs it shows: read that one (or none, for a chat with no transcript yet)."""
        if transcript:
            self.follow(transcript)
        else:
            self.forget_chat()

    def _evidence_when_ready(self):
        """The evidence goes to the worker once the rules have looked at the replies (it builds on their answers), for
        the replies it may have changed for: the new ones, and those after evidence that's new (a reply's evidence is
        what came before it). Lines streaming in come here once per 300 ms or so, not once a line."""
        if self.waiting and not self.halted:
            return True
        self._evidence_soon = None
        first, self._evidence_from = self._evidence_from or 1, None
        self.jobs.put(("evidence", {"replies": list(zip(self.chat_texts, self.chat_replies))[first - 1:],
                                    "first": first, "evidence": self.chat_evidence}, None))
        return False

    def other_checkouts(self):
        """The repository's other checkouts (its main one, its worktrees): paths Claude used there map here."""
        if self._checkouts is None:
            self._checkouts = []
            for line in (_git(self.root, "worktree", "list", "--porcelain") or "").splitlines():
                if line.startswith("worktree ") and os.path.realpath(line[9:]) != self.root:
                    self._checkouts.append(os.path.realpath(line[9:]))
        return self._checkouts

    def reading(self):
        """Whether the reader will decide this chat's names (on, and under its cap): till then, a plain word the
        rules placed isn't drawn as a link."""
        ai = self.agent
        return ai is not None and not ai.stopped and ai.cost < ai.budget

    def decision(self, message, surface):
        """What surface points at in a reply: its own decision, else the nearest earlier reply's in the chat (a name
        means the same until the reader says otherwise), else a later one's."""
        if not message:
            return None
        if (dec := self.decisions.get((message, surface))) is not None:
            return dec
        order = self._order if self._order_for is self.chat_replies else self._reorder()
        here, best = order.get(message), None
        if here is None:
            return None
        for (m, s_), d in self._by_surface.get(surface, ()):
            i = order.get(m)
            if i is None or d["by"] != "reader":
                continue
            rank = (0, here - i) if i < here else (1, i - here)  # (earlier first, then nearest)
            if best is None or rank < best[0]:
                best = (rank, d)
        return best[1] if best else None

    def _reorder(self):
        self._order_for, self._order = self.chat_replies, {h: i for i, h in enumerate(self.chat_replies)}
        return self._order

    def message_phrases(self, message):
        """A regex of the names decided in one reply as links or as not in the code (they're drawn in its prose
        too), or None."""
        if not message:
            return None
        if message not in self._phrase_res:
            names = sorted({s for s in self._by_surface if len(s) >= 3 and (d := self.decision(message, s)) is not None
                            and d["verdict"] != "plain"}, key=len, reverse=True)
            self._phrase_res[message] = re.compile(r"(?<![\w`-])(?:" + "|".join(map(re.escape, names)) +
                                                   r")(?![\w`-])") if names else None
        return self._phrase_res[message]

    def _apply_decisions(self, rows):
        for d in rows:
            key = (d["message"], d["surface"])
            if key not in self.decisions:
                self._by_surface.setdefault(d["surface"], []).append((key, d))
            else:
                self._by_surface[d["surface"]] = [(k, d if k == key else v) for k, v in self._by_surface[d["surface"]]]
            self.decisions[key] = d
            self._phrase_res.clear()  # (a decision may be carried into other replies)
            if d["by"] == "reader":
                self.decided_upto.add(d["message"])
        self.notify(sorted({d["surface"] for d in rows}))
        return False

    def catalogue_soon(self):
        """Catalogue the names afresh if it's been a while (main loop): the worker makes it, then catalogue_ready."""
        if not self.catalogue_busy and time.time() - self.catalogue_at > CATALOGUE_TTL:
            self.catalogue_busy = True
            self.jobs.put(("catalogue", None, None))

    def catalogue_ready(self, catalogue):
        """A new catalogue (None: it couldn't be made, tried again later): the replies read so far are matched against
        it, and the names new to it looked up."""
        self.catalogue_at, self.catalogue_busy = time.time(), False
        if catalogue is None:
            return False
        self.catalogue = catalogue
        if self.halted:  # (the replies shown, linked again from it)
            names = set()
            for text in list(self.shown_texts.values()):
                names.update(catalogue_hits(text, catalogue))
                names.update(s for s in ref_candidates(text, self.skipped, {}) if s in catalogue)
            self.link_catalogued(names)
            return False
        new = []
        for message, (text, session, written_at) in list(self.texts.items()):
            hints = {}
            cands = {s: c for s, c in catalogue_hits(text, catalogue, hints).items() if s not in self.refs}
            if not cands:
                continue
            for s, (kind, _strong) in cands.items():
                self.strong.add(s)
                self.refs[s] = Ref(s, kind)
                new.append(s)
            self.jobs.put(("resolve", cands, {"session": session, "written_at": written_at, "message": message,
                                              "hints": hints, "again": True}))
            self.waiting += 1
        if new:
            self.notify(new)
        self.status_changed()
        return False

    def known_from(self, surface):
        """Where the catalogue knows a name from: [(what, where)] (a Deployment and its manifest, a branch, a table and
        its database…), or []."""
        entry = self.catalogue.get(surface) or self.catalogue.get(ref_key(surface))
        return list(entry["sources"]) if entry else []

    def rescan_tools(self):
        """The tools, subagent types and skills known changed (the catalogue loaded, schemas fetched, the folder's
        definitions read): the replies read so far are looked at again for them."""
        batch, new = [], []
        for message, (text, session, written_at) in self.texts.items():
            cands = {s: c for s, c in ref_candidates(text).items()
                     if c[0] in REF_TOOL_KINDS and (s not in self.refs or self.refs[s].kind != c[0])}
            if cands:
                batch.append((cands, {"session": session, "written_at": written_at, "message": message, "again": True}))
                for s, (kind, strong) in cands.items():
                    if strong:
                        self.strong.add(s)
                    if s not in new:
                        self.refs[s] = Ref(s, kind)
                        new.append(s)
        for cands, meta in batch:
            self.jobs.put(("resolve", cands, meta))
        if new:
            self.notify(new)

    def refresh(self, surfaces, touched=()):
        """Search again, whatever was found (or given up on) before."""
        cands = {}
        for s in surfaces:
            if s in self.refs:
                kind = self.refs[s].kind  # (a tool's name may look like code: it stays a tool)
                cands[s] = (kind if kind in REF_TOOL_KINDS else ref_kind(s) or kind, s in self.strong)
                old = self.refs[s]
                self.refs[s] = Ref(s, old.kind)
        if cands:
            self.jobs.put(("resolve", cands, {"force": True, "touched": list(touched)}))
            self.notify(list(cands))

    def reanchor(self, surface, index, line):
        """A location was found at a new line: store that (and a fresh anchor)."""
        self.jobs.put(("reanchor", (surface, index, line), None))

    def mark_stale(self, surface):
        self.jobs.put(("stale", surface, None))

    def notify(self, surfaces):
        for fn in list(self.listeners):
            fn(surfaces)

    def status_changed(self):
        for fn in list(self.status_listeners):
            fn()

    def status_text(self):
        """One line on what it's doing: the Signal page's, and the header button's tooltip."""
        if self.halted:
            return f"Not running: the links are what needs no model (the glossary's, the catalogue's and git's: " \
                   f"{len(self.refs)} names)"
        part = self.agent.part if self.agent is not None else None
        now = self.activity or ("Waiting to start" if self.waiting else
                                f"{self.agent.model.capitalize()}: reading replies {part['a']} to {part['b']}" if part
                                else "Idle")
        more = f" · {self.waiting} {'reply' if self.waiting == 1 else 'replies'} waiting" if self.waiting else ""
        ai = self.agent
        read_to = (ai.part["a"] - 1 if ai.part else sum(1 for e in ai.events[:ai.pos] if e[0] == "reply")) if ai else 0
        model = (f" · {ai.model.capitalize()}: {len(ai.replies) - read_to} replies to read, {ref_money(ai.cost)}"
                 if ai.busy() else f" · {ai.model.capitalize()} {ref_money(ai.cost)}") if ai is not None and ai.parts \
            else ""
        return f"{now}{more} · {self.read} {'reply' if self.read == 1 else 'replies'} read{model}"

    def say(self, activity):
        """What it's doing now (main loop; None: nothing)."""
        self._told(activity, None, 0, time.time())

    def note(self, entry):
        """A line for the log of what Signal did (main loop)."""
        self._told(False, entry, 0, time.time())

    def reader_said(self, kind, text):
        """An entry of the reader's chat on the Signal page (main loop): part (the replies it's given), assistant (its
        message's content blocks, drawn as a subagent's are), results (its tool calls' results), glossary (what the
        checks kept of its decisions), done (a part read), refused."""
        self.reader_chat.append((time.time(), kind, text))
        del self.reader_chat[:-400]
        self.status_changed()
        return False

    def stop(self):
        """Turned off: drop the replies waiting, stop Sonnet and Haiku, the language servers and the lookup in
        progress, at once. What's been found stays in the glossary. Returns what was stopped (for a toast)."""
        self.halted = True
        while True:
            try:
                self.jobs.get_nowait()
            except queue.Empty:
                break
        ai, code = self.agent, self.code
        stopped = [f"{self.waiting} {'reply' if self.waiting == 1 else 'replies'} waiting"] if self.waiting else []
        if ai is not None and ai.busy():
            stopped.append(f"{ai.model.capitalize()}'s reading" + (f" (replies {ai.part['a']} to {ai.part['b']})"
                                                                     if ai.part else ""))
        if code is not None and (running := code.running()):
            stopped.append(f"{len(running)} language server{'s' * (len(running) != 1)}")
        if ai is not None:
            ai.stop()  # (what it spent still counts against the cap)
        if code is not None:
            code.stop()  # (started again when a code name next needs one)
        self.waiting, self.activity = 0, None
        self.note("Turned off" + (": stopped " + ", ".join(stopped) if stopped else ""))
        return stopped

    def checking(self):
        """Whether the model pass will check the rules' plain words and several-match names (Sonnet on, under its cap)."""
        ai = self.agent
        return ai is not None and ai.cost < ai.budget

    def links_only(self):
        """Signal shows its links but isn't running: whatever it's doing stops (stop), and the glossary is read again
        for drawing. Returns what was stopped."""
        stopped = self.stop() if not self.halted else []
        self.jobs.put(("load", None, None))
        return stopped

    def start(self):
        """Turned on again after stop: the glossary and the tools are read again; the window queues the replies."""
        if self.halted:
            self.halted, self._chat = False, None  # (the chat is read afresh: its evidence, the reader)
            self._chats.clear()
            self.jobs.put(("load", None, None))
            self.jobs.put(("tools", None, None))
            if self.agent is not None:
                self.agent.start()

    def set_agent(self, on):
        if on and self.agent is None:
            self.agent = SignalReader(self)
            self.follow(None)
        elif not on and self.agent is not None:
            self.agent.stop()
            self.agent = None
        self.status_changed()

    def set_lsp(self, on):
        """Your answer about language servers (the question, or the Signal page's switch): fetch them if they
        aren't there and look at the code names again, or stop them and find code names by search alone."""
        save_settings(signal_lsp=on)
        self.lsp_said.discard("fetch")
        self.jobs.put(("lsp", on, None))
        self.status_changed()

    def _ask_lsp(self):
        for fn in list(self.ask_listeners)[:1]:  # (one window asks)
            fn()
        return False

    def _lsp_ready(self):
        """The servers have just been fetched (or turned on): the code names in this run's replies are looked up
        again, with them."""
        self.refresh([s for s in self.strong if (r := self.refs.get(s)) is not None and r.kind == "symbol"
                      and r.status != "pending"])
        return False

    def _tell(self, activity=False, entry=None, read=0):
        """From the worker: what it's doing now (None: nothing), a line for the log, replies done."""
        GLib.idle_add(self._told, activity, entry, read, time.time())

    def _told(self, activity, entry, read, when):
        if activity is not False and not (self.halted and activity):  # (a lookup that was running when stopped)
            self.activity = activity
        if entry:
            self.log.append((when, entry))
            del self.log[:-400]
        self.waiting, self.read = max(0, self.waiting - read), self.read + read
        self.status_changed()
        return False

    def _apply(self, refs):
        for r in refs:
            self.refs[r.surface] = r
            if r.data.get("phrase") and r.surface not in self.phrases:
                self.phrases.add(r.surface)
                self.phrase_re = re.compile(r"(?<![\w`-])(?:" + "|".join(
                    re.escape(p) for p in sorted(self.phrases, key=len, reverse=True)) + r")(?![\w`-])")
        self.notify([r.surface for r in refs])
        return False

    def _post(self, refs):
        if refs:
            GLib.idle_add(self._apply, refs)

    # ---- either thread: where a location is now
    def old_text(self, blob):
        """The version of a file a location was read from: kept in .baude/blobs, or git's own if committed."""
        if not blob:
            return None
        import zlib
        try:
            with open(os.path.join(self.dir, "blobs", blob), "rb") as f:
                return zlib.decompress(f.read()).decode("utf-8", "replace")
        except (OSError, zlib.error):
            pass
        out = _git(self.top, "cat-file", "blob", blob, raw=True) if self.top else None
        return out.decode("utf-8", "replace") if out is not None else None

    def locate(self, loc):
        """Where a location is now: (absolute path, line, how), how being 'same', 'moved' or 'lost'. One on another
        branch (its rev): the same while that branch has its file (text_on shows it)."""
        full, line = os.path.join(self.root, loc["path"]), loc.get("line")
        if loc.get("rev"):
            there = self.top and _git(self.top, "cat-file", "-e", f"{loc['rev']}:{loc['path']}") is not None
            return full, line, "same" if there else "lost"
        if not os.path.exists(full):
            return full, line, "lost"
        if not line or not loc.get("blob") or os.path.isdir(full):
            return full, line, "same"
        try:
            with open(full, "rb") as f:
                data = f.read()
        except OSError:
            return full, line, "lost"
        if blob_sha(data) == loc["blob"]:
            return full, line, "same"
        new = data.decode("utf-8", "replace").splitlines()
        old = self.old_text(loc["blob"])
        found = ref_map_line(old.splitlines(), new, line) if old is not None else None
        found = found or ref_find_snippet(loc, new, bool(REF_SENSITIVE.search(loc["path"])))
        return (full, found, "moved") if found else (full, line, "lost")

    def text_on(self, loc):
        """A location's file as its branch (rev) has it; None if it hasn't it."""
        out = _git(self.top, "show", f"{loc['rev']}:{loc['path']}", raw=True) if self.top and loc.get("rev") else None
        return out.decode("utf-8", "replace") if out is not None else None

    # ---- worker thread
    def _run(self):
        lookup.git_wait(self.wait_still)
        while True:
            jobs = [self.jobs.get()]
            self.wait_still()  # (and what's queued meanwhile goes with it)
            if jobs[0][0] in ("resolve", "local"):  # every reply queued meanwhile, in one go: one git status for all
                while len(jobs) < 200:
                    try:
                        jobs.append(self.jobs.get_nowait())
                    except queue.Empty:
                        break
            batch = [(arg, meta) for job, arg, meta in jobs if job == "resolve"]
            self._local_st = None  # (the local lookups among them share one state of the repository, _job_local's:
            # one a reply was 3 gits, 150 of them as a long chat opened)
            for job, arg, meta in [("batch", batch, None)] * bool(batch) + [j for j in jobs if j[0] != "resolve"]:
                self.wait_still()
                try:
                    getattr(self, "_job_" + job)(arg, meta)
                except Exception as e:  # noqa: BLE001 (one bad name mustn't stop the rest)
                    print(f"baude: signal {job} failed: {e!r}", file=sys.stderr)
                    self._tell(entry=f"Error ({job}): {e!r}")
                    self.db = None  # opened afresh next time
                    if job == "batch":  # never leave names pending forever
                        self._post([Ref(s, kind, "not_found", reason=f"the lookup failed ({e})")
                                    for cands, _m in arg for s, (kind, _strong) in cands.items()])
                        self._tell(read=sum(1 for _c, m in arg if m and m.get("message")))
            if self.jobs.empty():
                self._tell(activity=None)

    def _job_catalogue(self, _arg, _meta):
        self._tell(activity="Cataloguing the names that are there…")
        t0, catalogue = time.monotonic(), None
        try:
            catalogue = self._build_catalogue(self._repo_state())
            self._tell(entry=f"Catalogued {len(catalogue)} names (files, branches, Kubernetes, databases) in "
                             f"{time.monotonic() - t0:.1f} s")
        finally:
            GLib.idle_add(self.catalogue_ready, catalogue)

    def _job_load(self, _arg, _meta):
        if self.halted and not signal_glossary_exists(self.folder, self.home):
            return  # (only showing links, and there's no glossary here: none is made)
        self._tell(activity="Opening the glossary…")
        db = self._open()
        refs = [self._load(s) for (s,) in db.execute("SELECT surface FROM alias WHERE checkout = ? AND scope = ?",
                                                    (self.checkout, self.scope))]
        self._post([r for r in refs if r is not None])
        self._job_decisions_load()
        where = f"{self.root} ({'git' if self.top else 'no git'}{', GitHub ' + self.github if self.github else ''})"
        scope = f", {self.scope}'s names" if self.scope else ""
        self._tell(entry=f"Glossary {pretty_path(self.dir)}{scope}: {len(refs)} names known. Searching {where}.")

    def _save(self, ref, fp):
        db, now = self._open(), time.time()
        row = db.execute("SELECT id, attempts FROM entity WHERE checkout = ? AND scope = ? AND kind = ? AND "
                         "canonical = ?", (self.checkout, self.scope, ref.kind, ref.canonical)).fetchone()
        missed = ref.attempted and ref.status == "not_found"
        attempts = (row[1] if row else 0) + (1 if missed else 0)
        values = (ref.status, ref.reason, json.dumps(ref.data), fp if ref.attempted else ref.fingerprint, attempts,
                  now if missed else None, now)
        if row:
            eid = row[0]
            db.execute("UPDATE entity SET status = ?, reason = ?, data = ?, fingerprint = ?, attempts = ?, "
                       "gave_up_at = COALESCE(?, gave_up_at), updated_at = ? WHERE id = ?", values + (eid,))
        else:
            eid = db.execute("INSERT INTO entity (status, reason, data, fingerprint, attempts, gave_up_at, updated_at, "
                             "kind, canonical, checkout, scope) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                             values + (ref.kind, ref.canonical, self.checkout, self.scope)).lastrowid
        db.execute("DELETE FROM location WHERE entity_id = ?", (eid,))
        for loc in ref.locations:
            cols = [f for f in REF_LOC_FIELDS if f != "id"]
            loc["id"] = db.execute(f"INSERT INTO location (entity_id, {', '.join(cols)}) VALUES (?{', ?' * len(cols)})",
                                   (eid, *(loc.get(f) for f in cols))).lastrowid
        db.execute("INSERT OR REPLACE INTO alias (checkout, scope, surface, entity_id) VALUES (?, ?, ?, ?)",
                   (self.checkout, self.scope, ref.surface, eid))
        ref.id = eid
        if ref.attempted:
            ref.fingerprint = fp

    def _rank(self, ref, texts=()):
        """Several matches: the ones nearest to where the same replies' other names are come first (a field in the
        model.py the reply's other fields are in, a system.yaml in the app folder the reply names). The replies:
        the ones it was looked up from, else texts (a name Sonnet listed). True if the order changed."""
        if ref.status != "ambiguous" or len(ref.locations) < 2:
            return False
        db = self._open()
        msgs = [m for (m,) in db.execute("SELECT DISTINCT message FROM mention WHERE entity_id = ?", (ref.id,))] \
            if ref.id is not None else []
        msgs = msgs or [hashlib.sha1(t.encode("utf-8", "replace")).hexdigest()[:16] for t in texts]
        if not msgs:
            return False
        ctx = {p for (p,) in db.execute(
            f"SELECT (SELECT path FROM location WHERE entity_id = e.id ORDER BY id LIMIT 1) FROM entity e "
            f"WHERE e.status = 'resolved' AND e.id != ? AND e.checkout = ? AND e.scope = ? AND e.id IN "
            f"(SELECT entity_id FROM mention WHERE message IN ({','.join('?' * len(msgs))}))",
            (ref.id or -1, self.checkout, self.scope, *msgs)) if p and not os.path.isabs(p)}

        def near(loc):  # strong evidence only (else the rules' order stands: a data model's field first...)
            if loc["path"] in ctx:
                return 1000
            if any(loc["path"].startswith(c + "/") for c in ctx):
                return 500  # (inside a folder the reply names)
            parts = loc["path"].split("/")
            shared = max((next((i for i, (a, b) in enumerate(zip(parts, c.split("/"))) if a != b),
                               min(len(parts), len(c.split("/")))) for c in ctx), default=0)
            return shared if shared >= 3 else 0
        order = sorted(range(len(ref.locations)), key=lambda i: (-near(ref.locations[i]), i))
        if order == list(range(len(order))):
            return False
        ref.locations = [ref.locations[i] for i in order]
        return True

    def _mention(self, ref, meta, st):
        if not meta or not meta.get("message") or ref.id is None:
            return
        self._open().execute("INSERT OR IGNORE INTO mention VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                             (ref.id, meta.get("session") or "", meta["message"], ref.surface, st["head"],
                              int(bool(st["status"])), meta.get("written_at"), time.time()))
        if meta.get("written_at") and not (ref.written_at and ref.written_at < meta["written_at"]):
            ref.written_at = meta["written_at"]

    def _job_batch(self, batch, _meta):
        """Look up the names in a batch of replies (and Refreshes): each name once, every mention recorded."""
        replies = sum(1 for _c, m in batch if m and m.get("message") and not m.get("again"))
        names, mentions, touched, hints = {}, [], set(), {}
        for cands, meta in batch:
            force = bool(meta and meta.get("force"))
            touched.update((meta or {}).get("touched") or ())
            hints.update((meta or {}).get("hints") or {})
            for s, (kind, _strong) in cands.items():
                names[s] = (names.get(s, (kind, False))[0], names.get(s, (kind, False))[1] or force)
                mentions.append((s, meta))
        what = f"{replies} {'reply' if replies == 1 else 'replies'}" if replies else \
            ("Earlier replies, for tools" if any(m and m.get("again") for _c, m in batch) else "Refresh")
        self._tell(activity=f"{what}: {len(names)} names. Reading the repository's state…")
        t0 = time.monotonic()
        st = self._repo_state()
        if (dt := time.monotonic() - t0) > 0.5:
            self._tell(entry=f"git status took {dt:.1f} s ({len(st['status'])} changed files)")
        meta = {"touched": sorted(touched), "hints": hints}
        out, enrich, known = {}, [], 0
        for i, (surface, (kind, force)) in enumerate(names.items(), 1):
            if self.halted:  # turned off: the names not looked up yet stay as they were
                break
            ref = None if force or kind in REF_TOOL_KINDS else self._load(surface)  # (the catalogue is newer)
            if ref is not None:
                if ref.status in ("resolved", "ambiguous", "stale"):
                    ref = self._validate(ref, st, meta)
                elif ref.status != "not_found" or ref.fingerprint != st["fp"]:
                    ref = None  # given up on, but the code has changed since: the cheap tiers again
            if ref is None:
                self._tell(activity=f"{what}: looking up {surface} ({REF_KINDS[kind][0].lower()}), {i} of {len(names)}")
                t0 = time.monotonic()
                ref = self._resolve(surface, kind, st, meta)
                ms = (time.monotonic() - t0) * 1000
                verdict = {"resolved": "Found", "ambiguous": "Several matches", "not_found": "Not found"}.get(
                    ref.status, ref.status.capitalize())
                self._tell(entry=f"{verdict}: {surface} ({REF_KINDS[ref.kind][0].lower()}): "
                                 f"{ref.summary().removeprefix('Not found: ')} [{ms:.0f} ms]")
            elif ref.attempted:  # validated, and found again by name
                self._tell(entry=f"Found again: {surface}: {ref.summary()}")
            else:
                known += 1
            self._save(ref, st["fp"])
            out[surface] = ref
            if self._needs_github(ref):
                enrich.append(ref)
        misses = {s: r.kind for s, r in out.items() if r.status == "not_found" and r.attempted and
                  r.kind in REF_BRANCH_KINDS}
        if misses and not self.halted:  # (not in this checkout: perhaps on a branch the chat names)
            for surface, ref in self._on_branches(misses, st).items():
                for loc in ref.locations:
                    self._anchor(loc, st)
                ref.attempted = True
                self._tell(entry=f"Found on {ref.locations[0]['rev']}: {surface}: {ref.summary()}")
                self._save(ref, st["fp"])
                out[surface] = ref
        for surface, m in mentions:
            if surface in out:
                self._mention(out[surface], m, st)
        for ref in out.values():
            if self._rank(ref):
                self._save(ref, st["fp"])
        self._open().commit()
        self._post(list(out.values()))
        if known:
            self._tell(entry=f"{what}: {known} of {len(names)} names already known")
        self._tell(read=replies)
        for ref in enrich:  # slow (the network): after everything local is shown
            if self.halted:
                break
            self._tell(activity=f"Asking GitHub about {ref.surface}…")
            self._tell(entry=f"GitHub: {self._enrich(ref)}")
            self._save(ref, st["fp"])
            self._open().commit()
            self._post([ref])

    def _job_local(self, names, meta):
        """Names in replies Signal isn't running for, looked up here alone (look_locally): those found are posted,
        marked local; nothing goes in the glossary, nothing is copied into .baude, nothing is fetched or asked."""
        self._tell(activity=f"Looking up {len(names)} names here alone (Signal isn't running)…")
        st = self._local_st = self._local_st or self._repo_state()  # (the replies drained with it share it: _run)
        found, misses = [], {}
        for surface, kind in names.items():
            try:
                ref = self._resolve(surface, kind, st, meta, keep=False)
            except Exception as e:  # noqa: BLE001 (one bad name mustn't stop the rest)
                print(f"baude: signal: looking up {surface} here failed: {e!r}", file=sys.stderr)
                continue
            if ref.status in ("resolved", "ambiguous"):
                found.append(ref)
            elif kind in REF_BRANCH_KINDS:
                misses[surface] = kind
        for ref in self._on_branches(misses, st).values():
            for loc in ref.locations:
                self._anchor(loc, st, keep=False)
            found.append(ref)
        for ref in found:
            ref.data["local"] = True
        self._post(found)
        self._tell(entry=f"Here alone: {len(found)} of {len(names)} names found (nothing kept)")

    def _branch_revs(self, st):
        """The branches a name this checkout hasn't may be on, in the order they're looked at: those the chat names
        (the latest first) and the heads of the pull requests it names, then the REF_BRANCHES most recently changed;
        one a commit, and not the one checked out."""
        if not self.top:
            return []
        shas, order = {}, []
        for row in (_git(self.top, "for-each-ref", "--sort=-committerdate", "--format=%(objectname) %(refname) "
                         "%(refname:short)", "refs/heads", "refs/remotes") or "").splitlines():
            sha, full, short = (row.split(" ", 2) + ["", ""])[:3]
            if short and not full.endswith("/HEAD") and sha != st["head"]:
                shas[short] = sha
                order.append(short)
        heads = [r.data["head"] for r in list(self.refs.values()) if r.kind == "pr" and r.data.get("head")]
        named = [c for b in list(self.chat_branches)[::-1] + heads[::-1]
                 if (c := next((c for c in (b, "origin/" + b) if c in shas), None))]
        revs, seen = [], set()
        for short in named + order[:REF_BRANCHES]:
            if shas[short] not in seen:
                seen.add(shas[short])
                revs.append(short)
        return revs

    def _on_branches(self, names, st):
        """Names this checkout hasn't ({surface: kind}: code names and files) looked for on other branches
        (_branch_revs), the code names in one search: each found there is that branch's (its locations' rev), at a
        definition of the name (else a module of that name) or a file of that name, on the first branch in that
        order that has one. {surface: Ref}."""
        revs = self._branch_revs(st) if names else []
        if not revs:
            return {}
        out, order = {}, {r: i for i, r in enumerate(revs)}
        code = {s: re.split(r"[.:]", re.sub(r"\([^()]*\)$", "", s))[-1] for s, k in names.items() if k == "symbol"}
        code = {s: n for s, n in code.items() if re.fullmatch(r"[A-Za-z_]\w{2,}", n)}
        if code:
            args = [a for n in sorted(set(code.values())) for a in ("-e", n)]
            raw = _git(self.top, "grep", "-n", "-I", "-w", "-F", *args, *revs, "--", *REF_CODE_PATHS, ok=(0, 1),
                       timeout=60) or ""
            defs = {}
            for line in raw.splitlines()[:50000]:
                parts = line.split(":", 3)
                if len(parts) < 4 or not parts[2].isdigit():
                    continue
                rev, path, n, text = parts
                for name in set(code.values()):
                    if name in text and (role := ref_def_role(path, text, name)):
                        defs.setdefault(name, []).append((order.get(rev, len(revs)), rev, path, int(n), role))
            for surface, name in code.items():
                hits = sorted(defs.get(name, ()))
                hits = [h for h in hits if h[1] == hits[0][1]] if hits else []
                hits = [h for h in hits if not ref_is_test(h[2])] or hits
                if not hits:
                    continue
                rev = hits[0][1]
                locs = [{"role": role, "path": path, "line": n, "resolver": "branch", "rev": rev}
                        for _o, _r, path, n, role in hits[:30]]
                out[surface] = Ref(surface, "symbol", "resolved" if len(hits) == 1 else "ambiguous",
                                   f"{hits[0][2]}::{name}" if len(hits) == 1 else name,
                                   reason=f"not in this checkout: on {rev}" + (f", {len(hits)} definitions of {name}"
                                                                               if len(hits) > 1 else ""),
                                   locations=locs)
        def tree(rev):  # (a branch's files, once a commit)
            if (files := self._cache.get(("tree", rev))) is None:
                files = self._cache[("tree", rev)] = (_git(self.top, "ls-tree", "-r", "--name-only", rev, timeout=30)
                                                      or "").splitlines()
            return files
        exts = tuple(p[1:] for p in REF_CODE_PATHS)
        for surface, name in code.items():  # (no definition: a module of that name, as _symbol finds one here)
            if surface in out:
                continue
            for rev in revs:
                if hits := sorted((f for f in tree(rev) if f.endswith(exts) and
                                   os.path.splitext(os.path.basename(f))[0] == name), key=lambda h: (len(h), h)):
                    out[surface] = Ref(surface, "symbol", "resolved" if len(hits) == 1 else "ambiguous", hits[0] if
                                       len(hits) == 1 else name, reason=f"not in this checkout: on {rev}",
                                       locations=[{"role": "module", "path": h, "line": None, "resolver": "branch",
                                                   "rev": rev} for h in hits[:30]])
                    break
        for surface, kind in names.items():
            if kind != "file":
                continue
            m = re.fullmatch(r"(.*?)(?::(\d+)(?::\d+)?)?", surface)
            want, line = m.group(1).removeprefix("./").rstrip("/"), int(m.group(2)) if m.group(2) else None
            for rev in revs:
                files = tree(rev)
                hits = [f for f in files if f == want or f.endswith("/" + want)]
                if hits:
                    hits.sort(key=lambda h: (not self._in_scope(h), len(h), h))
                    locs = [{"role": "file", "path": h, "line": line, "resolver": "branch", "rev": rev}
                            for h in hits[:40]]
                    out[surface] = Ref(surface, "file", "resolved" if len(hits) == 1 else "ambiguous",
                                       hits[0] + (f":{line}" if line else "") if len(hits) == 1 else want,
                                       reason=f"not in this checkout: on {rev}" + (f", {len(hits)} files end with "
                                                                                   f"{want}" if len(hits) > 1 else ""),
                                       locations=locs)
                    break
        return out

    def _job_git(self, names, _meta):
        """Commits and pull requests looked up in git alone (look_in_git): the ones found are linked."""
        known = self.transcript is not None  # (till the chat is known, neither are the repositories it worked in)
        found, missed = [], []
        for surface, kind in names.items():
            ref = self._commit(surface) if kind == "commit" else self._pr(surface)
            (found if ref.status == "resolved" else missed).append(ref)
        self._post(found)
        if missed and not known:
            GLib.idle_add(self._git_asked.difference_update, [r.surface for r in missed])

    def chat_repos(self):
        """The other git repositories the chat worked in, in the order it went to them (worker thread): where the
        paths in its tool calls are (a file it read or edited, a cd, a git -C) and the folders it ran in. A commit
        it names may be one of theirs. Read on from where it was, as the chat grows."""
        path = self.transcript
        if not path:
            return []
        seen = self._repos.setdefault(path, {"offset": 0, "tops": [], "dirs": {}})
        lines, offset = [], seen["offset"]
        try:
            with open(path, "rb") as f:
                f.seek(offset)
                for raw in f:  # (a line at a time: a long chat decoded and split whole held Python's lock 0.2 s)
                    if not raw.endswith(b"\n"):
                        break  # (whole lines only: the rest is read next time)
                    offset += len(raw)
                    lines += raw.decode("utf-8", "replace").splitlines()
        except OSError:
            return seen["tops"]
        seen["offset"] = offset
        home = os.path.expanduser("~")
        mine = {os.path.realpath(t) for t in (self.top, *self.other_checkouts()) if t}
        for line in lines:
            paths = re.findall(r'"cwd":\s*"([^"]+)"', line[:3000])
            if re.search(r'"type":\s*"tool_use"', line) and len(line) < 500_000:
                paths += re.findall(r'(?<![\w/.~:-])((?:~|/[\w.-]+)/[^\s"\'\\:;|&)<>`,]+)', line)
            for p in paths:
                p = os.path.join(home, p[2:]) if p.startswith("~/") else p
                d = asked = p if os.path.isdir(p) else os.path.dirname(p)
                if d in seen["dirs"] or any(d == t or d.startswith(t + os.sep) for t in mine | set(seen["tops"])):
                    continue
                while d and d not in (home, "/") and not os.path.isdir(d):
                    d = os.path.dirname(d)
                if d not in seen["dirs"]:
                    seen["dirs"][d] = (_git(d, "rev-parse", "--show-toplevel") or "").strip() \
                        if d not in (home, "/", "") else ""
                top = seen["dirs"][asked] = seen["dirs"][d]
                top = os.path.realpath(top) if top else ""
                if top and top not in mine and top not in seen["tops"]:
                    seen["tops"].append(top)
        return seen["tops"]

    # ---- the reader's tools: a socket the relay (SIGNAL_READER_MCP) talks to; the worker answers
    def tool_socket_dir(self):
        return os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()

    def tool_socket(self):
        """The socket the reader's tools come in on (started the first time it's asked for)."""
        if self._sock_path is None:
            path = os.path.join(self.tool_socket_dir(),
                                f"baude-signal-{os.getpid()}-{hashlib.sha1(self.dir.encode()).hexdigest()[:8]}.sock")
            try:
                os.unlink(path)
            except OSError:
                pass
            srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            srv.bind(path)
            os.chmod(path, 0o600)
            srv.listen(8)
            threading.Thread(target=self._serve, args=(srv,), daemon=True, name="baude-signal-tools").start()
            self._sock_path = path
        return self._sock_path

    def _serve(self, srv):
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            threading.Thread(target=self._serve_one, args=(conn,), daemon=True).start()

    def _serve_one(self, conn):
        try:
            data = b""
            while not data.endswith(b"\n"):
                chunk = conn.recv(1 << 16)
                if not chunk:
                    break
                data += chunk
            req = json.loads(data or b"{}")
            tool, args = req.get("tool"), req.get("args") or {}
            if tool == "_tools":
                res = {"tools": SIGNAL_READER_TOOLS}
            elif tool == "schema":
                res = self._tool_schema(args)  # (docker, not the glossary: here)
            else:
                box = {"event": threading.Event()}
                self.jobs.put(("tool", (tool, args, box), None))
                res = box["result"] if box["event"].wait(240) and "result" in box else \
                    {"error": "Baude is busy; try again in a moment"}
            conn.sendall((json.dumps(res, ensure_ascii=False, default=str) + "\n").encode())
        except (OSError, ValueError) as e:
            try:
                conn.sendall((json.dumps({"error": str(e)}) + "\n").encode())
            except OSError:
                pass
        finally:
            conn.close()

    def _job_tool(self, arg, _meta):
        tool, args, box = arg
        try:
            fn = {"known": self._tool_known, "lookup": self._tool_lookup, "decide": self._tool_decide,
                  "kube": self._tool_kube}.get(tool)
            box["result"] = fn(args) if fn else {"error": f"no tool {tool}"}
        except Exception as e:  # noqa: BLE001 (the reader is told; the worker goes on)
            box["result"] = {"error": f"{type(e).__name__}: {e}"}
        box["event"].set()

    @staticmethod
    def _places(ref, n=5):
        return [f"{l['path']}" + (f":{l['line']}" if l.get("line") else "") + f" ({l['role']}"
                + (f", {l['note']}" if l.get("note") else "") + ")" for l in ref.locations[:n]]

    def _tool_known(self, args):
        ai = self.agent
        reply = args.get("reply")
        number = {h: i + 1 for i, h in enumerate(ai.replies)} if ai is not None else {}
        out = []
        for name in [str(n) for n in (args.get("names") or [])][:60]:
            key = ref_key(name.strip("`"))
            item = {"name": name}
            ref = self._load(key)
            if ref is not None:
                item.update(kind=ref.kind, status=ref.status, places=self._places(ref), why=ref.reason or None,
                            note=ref.data.get("note"))
            decs = self._open().execute("SELECT message, verdict, path, line, role, note, why FROM decision WHERE "
                                        "checkout = ? AND scope = ? AND surface = ? ORDER BY at DESC LIMIT 4",
                                        (self.checkout, self.scope, key)).fetchall()
            if decs:
                item["decided"] = [{"reply": number.get(m), "verdict": v,
                                    **({"place": p + (f":{n}" if n else "") + (f" ({r})" if r else "")} if p else {}),
                                    **({"why": w} if w else {}), **({"note": nt} if nt else {})}
                                   for m, v, p, n, r, nt, w in decs]
            if ai is not None and ai.full and isinstance(reply, int):
                seen = ref_seen(ai.evidence, key, reply)
                if seen:
                    item["seen"] = [p + (f":{n}" if n else "") for p, n in seen[:5]]
            out.append(item)
        return {"names": out}

    def _tool_lookup(self, args):
        name = ref_key(str(args.get("name") or "").strip("`"))
        if not name:
            return {"error": "no name"}
        kind = REF_AGENT_KINDS.get(str(args.get("kind") or "").lower()) or ref_kind(name) or "symbol"
        if kind not in REF_RULE_KINDS:
            kind = "symbol"
        self._looked.add(name)
        ref = self._resolve(name, kind, self._repo_state(), {})
        return {"name": name, "kind": ref.kind, "status": ref.status, "places": self._places(ref, 12),
                "why": ref.reason or None}

    def _tool_kube(self, args):
        """The reader's view of the repository's Kubernetes config: the objects with a name like name (part of one; a
        pod's name finds its workload), of a kind, in a namespace, and the kubeconfig's contexts (names only); with
        none of those, an overview: the charts and their releases, the kinds defined, the contexts."""
        st = self._repo_state()
        idx = self._kube_index(st)
        want = ref_key(str(args.get("name") or "")).strip("`").lower()
        kind = str(args.get("kind") or "").strip()
        kind = KUBE_KINDS.get(kind.lower(), kind) if kind else None
        ns = str(args.get("namespace") or "").strip() or None

        def row(o, name, how=""):
            r = {"kind": o["kind"], "name": name, "place": f"{o['path']}:{o['line']}"}
            if o["namespace"] and not _KUBE_ACTION.search(o["namespace"]):
                r["namespace"] = o["namespace"]
            for k in ("chart", "release", "overlay"):
                if o.get(k):
                    r[k] = o[k]
            if how:
                r["how"] = how
            return r

        def ctx(c):
            return {k: c[k] for k in ("name", "cluster", "namespace", "user", "current") if c.get(k)}
        if not (want or kind or ns):
            kinds = {}
            for o in idx["objects"]:
                if o["names"]:
                    kinds[o["kind"]] = kinds.get(o["kind"], 0) + 1
            return {"charts": [{"chart": r or ".", "name": c["name"], "values": c["values_files"],
                                "releases": [x["release"] + (f" (namespace {x['namespace']})" if x["namespace"] else "")
                                             for x in c["releases"]]} for r, c in idx["charts"].items()],
                    "kinds": kinds, "contexts": [ctx(c) for c in kube_contexts()][:20],
                    "note": "give a name (or part of one), a kind or a namespace for the objects themselves"}
        rows, rendered = [], {(o["path"], o["line"]) for o in idx["objects"] if o["names"]}
        for o in idx["objects"]:
            if (kind and o["kind"] != kind) or (ns and o["namespace"] and o["namespace"] != ns) or \
                    (not o["names"] and (o["path"], o["line"]) in rendered):  # (a template a release renders: as rendered)
                continue
            names = [n for n, _how in o["names"]] or [o["raw"]]
            if not want or any(want in n.lower() for n in names):
                rows.append(row(o, next((n for n in names if want in n.lower()), names[0]),
                                "" if o["names"] else "a templated name no release renders"))
        if want and not rows:  # (a pod's name, or a name a template matches by its literal parts)
            rows = [row(o, n, how) for o, n, how in self._kube_find(want, kind, ns, idx)]
        contexts = [ctx(c) for c in kube_contexts() if not want or want in c["name"].lower()] if not kind else []
        out = {"objects": rows[:40], "contexts": contexts[:20]}
        if len(rows) > 40:
            out["more"] = len(rows) - 40
        if not rows and not contexts:
            out["why"] = "no object or context like that in this repository's manifests, charts, overlays or kubeconfig"
        return out

    def _kube_object_at(self, path, line, st):
        """Whether a Kubernetes object the repository defines has its name on that line (a place kube gave)."""
        return bool(line) and any(o["path"] == path and o["line"] == line for o in self._kube_index(st)["objects"])

    def _tool_schema(self, args):
        table, column = (args.get("table") or "").strip().lower(), (args.get("column") or "").strip().lower()
        if not table and not column:
            return {"error": "give a table or a column"}
        try:
            servers = pg_servers()
        except RuntimeError as e:
            return {"error": str(e)}
        found = []
        for s in servers:
            for db in pg_databases(s):
                key = (s["container"], db)
                if key not in self._schemas:
                    try:
                        self._schemas[key] = pg_schema(s, db)
                    except (RuntimeError, ValueError):
                        self._schemas[key] = {"tables": {}}
                for (ns, name), t in self._schemas[key]["tables"].items():
                    cols = [c["name"] for c in t["columns"]]
                    if (table and (name.lower() == table or table in name.lower())) or \
                            (column and any(c.lower() == column for c in cols)):
                        found.append({"server": s["container"], "database": db, "schema": ns, "table": name,
                                      "columns": [f"{c['name']} {c['type']}" for c in t["columns"]][:80]})
        return {"tables": found[:12]} if found else {"tables": [], "why": "no reachable database has it"}

    def _snap(self, full, line, surface):
        """The line nearest to line in full that has the name (its last part) as a word; None if none does."""
        last = re.split(r"[.:/]", re.sub(r"\(.*\)$", "", surface).strip("`/"))[-1] or surface
        try:
            with open(full, encoding="utf-8", errors="replace") as f:
                lines = f.read(self.BLOB_LIMIT).splitlines()
        except OSError:
            return None
        pat = re.compile(r"(?<![\w$])" + re.escape(last) + r"(?![\w$])")
        hits = [i + 1 for i, l in enumerate(lines) if pat.search(l)]
        if not hits:
            return None
        return min(hits, key=lambda n: abs(n - (line or 1)))

    def _searched(self, surface):
        """Whether the name was searched for: a lookup, the reader's own Grep, or the rules looked and gave up."""
        ai = self.agent
        word = re.split(r"[.:/ ]", surface)[-1].lower() or surface.lower()
        if surface in self._looked or (ai is not None and any(word in p.lower() for p in ai.searches)):
            return True
        old = self._load(surface)
        return old is not None and old.status == "not_found"

    def _tool_decide(self, args):
        """The reader's decisions, checked one by one; kept for their reply."""
        ai = self.agent
        if ai is None:
            return {"error": "the reader is off"}
        st, kept, rejected, refs, rows = self._repo_state(), 0, [], [], []
        for i, d in enumerate(args.get("decisions") or []):
            if not isinstance(d, dict):
                continue
            try:
                n = int(d.get("reply"))
            except (TypeError, ValueError):
                rejected.append({"i": i, "why": "reply must be a number"})
                continue
            name = str(d.get("name") or "").strip().strip("`")
            if not 1 <= n <= len(ai.texts):
                rejected.append({"i": i, "name": name, "why": f"there is no reply {n}"})
                continue
            surface = ref_surface_in(ref_key(name), [ai.texts[n - 1]])
            if not surface or ref_secretish(surface):
                rejected.append({"i": i, "name": name, "why": f"reply {n} doesn't write {name!r}"})
                continue
            verdict, note = d.get("verdict"), _first_line(str(d.get("note") or ""), 160)
            kind = REF_AGENT_KINDS.get(str(d.get("kind") or "").lower())
            old = self._load(surface)
            if old is not None and old.status == "resolved" and (old.kind in ("commit", "branch", "pr") or
                                                                 old.data.get("context")):
                kept += 1  # (a git ref or a kubeconfig context Baude resolved itself: its link stands)
                continue
            if verdict == "link" and kind in ("commit", "branch", "pr"):  # (one the rules missed: git settles it)
                ref = self._resolve(surface, kind, st, {})
                if ref.status != "resolved":
                    rejected.append({"i": i, "name": name, "why": f"git has no {kind} {surface!r}"})
                    continue
                ref.attempted = True
                self._save(ref, st["fp"])
                refs.append(ref)
                kept += 1
                continue
            row = {"message": ai.replies[n - 1], "surface": surface, "verdict": verdict,
                   "kind": REF_AGENT_KINDS.get(str(d.get("kind") or "").lower(), str(d.get("kind") or "")[:20] or None),
                   "path": None, "line": None, "role": None, "note": note, "why": None, "by": "reader",
                   "at": time.time()}
            if verdict == "link":
                rel = self._relpath(str(d.get("path") or ""))
                full = os.path.join(self.root, rel) if rel else None
                if not full or not os.path.exists(full):
                    rejected.append({"i": i, "name": name, "why": f"{d.get('path')!r} isn't in this checkout ("
                                     f"{self.root}); not_in_code with why elsewhere, if it's somewhere else"})
                    continue
                line = None
                if os.path.isfile(full):
                    try:
                        line = int(d.get("line")) if d.get("line") else None
                    except (TypeError, ValueError):
                        line = None
                    if (d.get("role") or "") not in ("file", "folder") and not self._kube_object_at(rel, line, st):
                        line = self._snap(full, line, surface)  # (a Kubernetes object's place, as kube gave it, stands)
                        if line is None:
                            rejected.append({"i": i, "name": name, "why": f"{rel} doesn't have {surface!r}: find "
                                             "the file that does"})
                            continue
                row.update(path=rel, line=line, role=(d.get("role") or ("folder" if os.path.isdir(full) else
                                                                         "definition"))[:20])
                refs.append(self._reader_entity(surface, row, st))
            elif verdict == "not_in_code":
                why = d.get("why") if d.get("why") in ("proposed", "external", "elsewhere", "not_found") else "not_found"
                if why != "external" and not self._searched(surface):
                    rejected.append({"i": i, "name": name, "why": f"search for {surface!r} first (lookup, or Grep): "
                                     "it may be in the code"})
                    continue
                row["why"] = why
                refs.append(self._absent_entity(surface, row, old, st))
            elif verdict == "plain":
                if ref_codeish(surface):
                    rejected.append({"i": i, "name": name, "why": f"{surface!r} is written as code, not English: "
                                     "link it, or not_in_code with why proposed, external, elsewhere or not_found"})
                    continue
            else:
                rejected.append({"i": i, "name": name, "why": "verdict is link, plain or not_in_code"})
                continue
            self._store_decision(row)
            rows.append(row)
            kept += 1
        self._open().commit()
        self._post([r for r in refs if r is not None])
        if rows:
            GLib.idle_add(self._apply_decisions, rows)
            v = __import__("collections").Counter(r["verdict"] for r in rows)
            self._tell(entry=f"{ai.model.capitalize()} decided {kept}: {v.get('link', 0)} links, {v.get('plain', 0)} "
                             f"plain words, {v.get('not_in_code', 0)} not in the code" +
                             (f"; {len(rejected)} rejected" if rejected else ""))
            GLib.idle_add(self.reader_said, "glossary", {"rows": rows, "rejected": len(rejected)})
        answer = {"kept": kept, "rejected": rejected}
        if (left := self._undecided(ai)):
            answer["undecided"] = left
        return answer

    def _undecided(self, ai, limit=40):
        """The names in the part being read that have no decision yet (the reply's own, or one carried from an earlier
        reply): its backticked names and the rules' (routes in code blocks), but for those the rules placed in one
        file, the git refs Baude resolved, Claude Code's tools, and paths that already link."""
        part = ai.part
        if not part:
            return []
        db, left = self._open(), []
        decided = {}
        for m, surface in db.execute("SELECT message, surface FROM decision WHERE by = 'reader' AND checkout = ? AND "
                                     "scope = ?", (self.checkout, self.scope)):
            decided.setdefault(surface, set()).add(m)
        for n in range(part["a"], min(part["b"], len(ai.texts)) + 1):
            text, before = ai.texts[n - 1], set(ai.replies[:n])
            names = dict(ref_candidates(text))
            for s, c in catalogue_hits(text, self.catalogue).items():  # (the names known to be there, too)
                names.setdefault(s, c)
            for raw in re.findall(r"`([^`\n]{2,80})`", re.sub(r"(?ms)^\s*```.*?(?:^\s*```[^\n]*$|\Z)", "", text)):
                key = ref_key(raw)
                if key not in names and not resolve_path(raw) and not ref_secretish(key) and \
                        re.search(r"[A-Za-z]", key) and not re.fullmatch(r"\d[\d.,:%\sA-Za-z]*", key):
                    names[key] = (ref_kind(key) or "name", False)
            for surface, (kind, _strong) in names.items():
                if kind in REF_TOOL_KINDS or decided.get(surface, set()) & before:
                    continue
                ref = self._load(surface)
                if ref is not None and ref.status == "resolved" and (ref.kind in ("commit", "branch", "pr") or
                                                                     ref.data.get("context") or
                                                                     len({l["path"] for l in ref.locations}) == 1):
                    continue
                left.append(f"reply {n}: {surface}")
                if len(left) >= limit:
                    return left + ["(and more)"]
        return left

    def _absent_entity(self, surface, row, old, st):
        """The glossary's entry for a name the reader says isn't in the code: an outside tool (external), or not found
        with its reason; made only where the rules have nothing better (what it means in a reply is the decision)."""
        if old is not None and old.status not in ("pending", "not_found"):
            return None
        if row["why"] == "external":
            ref = Ref(surface, "external", "resolved", surface, data={"by": "reader", "note": row["note"] or
                                                                      "An outside tool or product"})
        else:
            why = {"proposed": "proposed in the conversation, not written yet", "elsewhere": "somewhere else (another "
                   "checkout, branch or machine)"}.get(row["why"], "not in this folder's code")
            ref = Ref(surface, row["kind"] if row["kind"] in REF_KINDS else (old.kind if old else ref_kind(surface) or
                                                                            "symbol"), "not_found",
                      reason=why + (f": {row['note']}" if row["note"] else ""), data={"by": "reader"})
        ref.attempted = True
        self._save(ref, st["fp"])
        return ref

    def _relpath(self, path):
        """A path the reader gave, relative to the checkout (absolute ones from another checkout of it too)."""
        path = path.strip().strip("`")
        if not path:
            return None
        if os.path.isabs(path):
            for top in (self.root, *self.other_checkouts()):
                if path.startswith(top.rstrip("/") + os.sep) or path == top:
                    return os.path.relpath(path, top)
            return None
        return os.path.normpath(path)

    def _reader_entity(self, surface, row, st):
        """The glossary's entry for a name the reader linked: made if the rules had none, its place added if new."""
        loc = {"role": row["role"], "path": row["path"], "line": row["line"], "resolver": "reader",
               "note": row["note"] or ""}
        self._anchor(loc, st)
        old = self._load(surface)
        if old is None or old.status in ("not_found", "pending"):
            ref = Ref(surface, row["kind"] if row["kind"] in REF_KINDS else (old.kind if old else "symbol"), "resolved",
                      f"{row['path']}::{surface}", data={"by": "reader", "note": row["note"]}, locations=[loc])
        elif any(l["path"] == loc["path"] for l in old.locations):
            return None
        else:
            ref = old
            ref.locations.append(loc)
        ref.attempted = True
        self._save(ref, st["fp"])
        return ref

    def _store_decision(self, row):
        """A decision for one reply; the reader's are kept over the evidence's (worker)."""
        db = self._open()
        old = db.execute("SELECT by FROM decision WHERE checkout = ? AND scope = ? AND message = ? AND surface = ?",
                         (self.checkout, self.scope, row["message"], row["surface"])).fetchone()
        if old and old[0] == "reader" and row["by"] != "reader":
            return False
        db.execute(f"INSERT OR REPLACE INTO decision (checkout, scope, {', '.join(REF_DECISION_FIELDS)}) VALUES "
                   f"(?, ?, {', '.join('?' * len(REF_DECISION_FIELDS))})",
                   (self.checkout, self.scope, *(row[f] for f in REF_DECISION_FIELDS)))
        return True

    def _job_decisions_load(self):
        rows = [dict(zip(REF_DECISION_FIELDS, r)) for r in
                self._open().execute(f"SELECT {', '.join(REF_DECISION_FIELDS)} FROM decision WHERE checkout = ? AND "
                                     f"scope = ?", (self.checkout, self.scope))]
        if rows:
            GLib.idle_add(self._apply_decisions, rows)

    def _job_evidence(self, arg, _meta):
        """What Claude read or grepped before a reply, as decisions where it's clear: a name shaped like code (not a
        plain word) that it saw in exactly one file is linked there, for that reply. The reader may decide otherwise."""
        if not load_settings().get("signal_evidence", True):
            return
        evidence, rows = arg["evidence"], []
        if not evidence:
            return
        db = self._open()
        for n, (text, message) in enumerate(arg["replies"], arg.get("first", 1)):  # (those it may have changed for)
            for surface, (kind, strong) in ref_candidates(text).items():
                if re.fullmatch(r"[A-Za-z][a-z]+", surface) or kind in ("commit", "branch", "pr", "tool"):
                    continue
                seen = ref_seen(evidence, surface, n)
                if not seen or db.execute("SELECT 1 FROM decision WHERE checkout = ? AND scope = ? AND message = ? AND "
                                          "surface = ?", (self.checkout, self.scope, message, surface)).fetchone():
                    continue
                ref = self._load(surface)
                if ref is not None and ref.status == "resolved" and len({l["path"] for l in ref.locations}) == 1:
                    continue  # (the rules know it)
                if ref is not None and ref.status == "ambiguous":  # one of the rules' matches: the one Claude saw
                    paths = {l["path"] for l in ref.locations}
                    seen = [(p, l) for p, l in seen if p in paths]
                if len({p for p, _l in seen}) != 1:
                    continue
                path, line = next(((p, l) for p, l in seen if l), seen[0])
                if not os.path.exists(os.path.join(self.root, path)):
                    continue
                row = {"message": message, "surface": surface, "verdict": "link", "kind": kind, "path": path,
                       "line": line, "role": "seen", "note": "where Claude looked", "why": None, "by": "session",
                       "at": time.time()}
                if self._store_decision(row):
                    rows.append(row)
        db.commit()
        if rows:
            GLib.idle_add(self._apply_decisions, rows)
            self._tell(entry=f"From what Claude read and grepped: {len(rows)} names placed in their replies")

    def _job_reader_part(self, arg, _meta):
        """A part, as the reader gets it: what happened, then the names the rules found in its replies with their
        answers (and, reading in full, the places Claude saw that mention them)."""
        ai = self.agent
        if ai is None:
            return
        lines = []
        for ev in arg["events"]:
            if ev[0] != "reply":
                continue
            cands = list(ref_candidates(ev[1]).items())[:30]
            if not cands:
                continue
            bits = []
            for surface, (kind, _strong) in cands:
                ref = self._load(surface)
                if kind == "k8s" and ref is not None and ref.status == "not_found" and ref_quiet(surface):
                    continue  # (a kebab-case name that's no object here: as likely a package's, so it tells nothing)
                if ref is None or ref.status == "pending":
                    bits.append(f"`{surface}` (not looked up)")
                elif ref.status == "not_found":
                    bits.append(f"`{surface}`: not found ({_first_line(ref.reason, 60)})")
                else:
                    first = ref.locations[0] if ref.locations else None
                    where = (first["path"] + (f":{first['line']}" if first.get("line") else "")) if first else ""
                    many = f"{len(ref.locations)} matches, first " if ref.status == "ambiguous" else ""
                    bits.append(f"`{surface}`: {many}{where}".rstrip(": "))
                if arg["full"]:
                    seen = ref_seen(ai.evidence, surface, ev[2])
                    if seen:
                        bits[-1] += " — Claude saw it at " + ", ".join(p + (f":{n}" if n else "") for p, n in seen[:2])
            lines.append(f"- reply {ev[2]}: " + "; ".join(bits))
        names = ("The backticked names Baude's rules found (and the routes in code blocks), with their answers "
                 "(decide what each means there; the names written in prose are yours to add):\n" + "\n".join(lines) +
                 "\n") if lines else \
            "Baude's rules found no backticked names here: the names are yours to find.\n"
        if arg.get("glossary") and arg["a"] > 1:  # (read on its own: the glossary stands in for the replies before)
            names += "\n" + self._earlier_decisions(arg, ai) + "\n"
        text = SIGNAL_READER_PART.format(k=arg["k"], a=arg["a"], b=arg["b"],
                                      events=signal_reader_render(arg["events"], arg["full"]), names=names)
        GLib.idle_add(ai.send, arg["k"], arg["a"], arg["b"], text)

    def _earlier_decisions(self, arg, ai, limit=80):
        """The glossary as a part read on its own needs it: the reader's latest decision before the part for each
        name its replies write."""
        texts = "\n".join(ev[1] for ev in arg["events"] if ev[0] == "reply")
        number = {h: i + 1 for i, h in enumerate(ai.replies)}
        latest = {}
        for m, surface, verdict, path, line, role, why, note in self._open().execute(
                "SELECT message, surface, verdict, path, line, role, why, note FROM decision WHERE by = 'reader' AND "
                "checkout = ? AND scope = ?", (self.checkout, self.scope)):
            n = number.get(m)
            if n is None or n >= arg["a"] or (surface in latest and latest[surface][0] > n) or surface not in texts:
                continue
            latest[surface] = (n, verdict, path, line, role, why, note)
        bits = []
        for surface, (n, verdict, path, line, role, why, note) in sorted(latest.items(), key=lambda x: -x[1][0]):
            if not re.search(r"(?<![\w`-])" + re.escape(surface) + r"(?![\w`-])", texts):
                continue
            what = (f"{path}" + (f":{line}" if line else "") + (f" ({role})" if role else "")) if verdict == "link" else \
                "plain English" if verdict == "plain" else f"not in the code ({why})"
            bits.append(f"`{surface}` (reply {n}): {what}" + (f" — {_first_line(note, 60)}" if note else ""))
            if len(bits) >= limit:
                break
        if not bits:
            return "Decided in earlier replies, for names in this part: none."
        return "Decided in earlier replies, for names in this part:\n" + "\n".join(f"- {b}" for b in bits)

    def _job_tools(self, _arg, _meta):
        """The cached tool schemas, this folder's subagent and skill definitions, and (with your OK) the tools of
        the MCP servers configured for it."""
        if self.halted:
            return
        if not TOOLS.loaded:
            TOOLS.load()
        agents, skills = local_definitions(self.root)
        GLib.idle_add(self._locals_ready, agents, skills)
        servers = {n: c for n, c in mcp_servers(self.root).items() if c.get("command")}
        if not servers:
            return
        on = load_settings().get("signal_mcp_schemas")
        if on is None and "mcp" not in self.tools_said:
            self.tools_said.add("mcp")
            GLib.idle_add(self._ask, "mcp", servers)
        elif on:
            for name, conf in servers.items():
                self._tell(activity=f"Reading the tools of the MCP server {name}…")
                tools = mcp_tool_schemas(conf, self.root)
                TOOLS.add_mcp(name, tools)
                self._tell(entry=f"MCP server {name}: {len(tools)} tools" if tools else
                           f"MCP server {name}: no tools listed (it didn't start, or isn't stdio)")
            GLib.idle_add(self._tools_ready)

    def _locals_ready(self, agents, skills):
        SUBAGENTS.clear()
        SUBAGENTS.update(agents)
        SKILLS.clear()
        SKILLS.update(skills)
        self.rescan_tools()
        return False

    def _want_tools(self):
        """A tool was named and its schema isn't here: fetch it if you said yes, ask you if you haven't said.
        Not when this Claude Code's schemas are here already (a tool they don't have isn't in Anthropic's types)."""
        if TOOLS.loaded and TOOLS.cli == claude_version():
            return
        on = load_settings().get("signal_tool_schemas")
        if on and "fetch" not in self.tools_said:
            self.tools_said.add("fetch")
            self._tell(activity="Fetching the tool schemas from Anthropic…")
            ok, what = tool_fetch(claude_version())
            self._tell(entry=f"Tool schemas: {what}")
            if ok and TOOLS.load():
                GLib.idle_add(self._tools_ready)
        elif on is None and "asked" not in self.tools_said:
            self.tools_said.add("asked")
            GLib.idle_add(self._ask, "tools", claude_version())

    def _job_tools_fetch(self, _arg, _meta):
        self._want_tools()

    def _tools_ready(self):
        """Schemas arrived: the tools named so far are looked up again, and earlier replies looked at for the
        tools they name (main loop)."""
        self.refresh([s for s, r in self.refs.items() if r.kind == "tool"])
        self.rescan_tools()
        return False

    def _ask(self, topic, detail):
        for fn in list(self.question_listeners)[:1]:  # (one window asks)
            fn(topic, detail)
        return False

    def set_tool_schemas(self, on):
        """Your answer about fetching tool schemas (the question, or the Signal page's switch)."""
        save_settings(signal_tool_schemas=on)
        if on:
            self.tools_said.discard("fetch")
            self.jobs.put(("tools_fetch", None, None))
        self.status_changed()

    def set_mcp_schemas(self, on):
        save_settings(signal_mcp_schemas=on)
        if on:
            self.jobs.put(("tools", None, None))

    def _tool(self, surface, fetch=True):
        """A tool by name ("Agent", "the Agent tool", mcp__server__tool): its schema card, from the catalogue
        (Anthropic's SDK types and docs, or an MCP server's tools/list), else what its calls show."""
        name = re.sub(r"\s+tools?$", "", re.sub(r"^(the|a|an)\s+", "", surface.strip("`").strip(), flags=re.I))
        info = TOOLS.get(name)
        if info is None and not TOOLS.loaded:  # (the cache first)
            TOOLS.load()
            info = TOOLS.get(name)
        if info is None and fetch and not name.startswith("mcp__"):
            self._want_tools()
            info = TOOLS.get(name)
        if info is None and name not in TOOL_NAMES:
            return Ref(surface, "tool", "not_found", reason=f"no tool named {name} in this Claude Code")
        data = {**(info or {}), "tool": name, "phrase": surface != name, "cli": TOOLS.cli, "sdk": TOOLS.sdk}
        if info is None:
            data["source"] = "calls"
            if name.startswith("mcp__"):
                data["server"] = name.split("__")[1]
        return Ref(surface, "tool", "resolved", name, data=data)

    def _definition(self, surface, kind, table):
        """A subagent type or a skill: its definition file (a built-in subagent type has none)."""
        name = surface.strip("`").strip()
        info = table.get(name)
        if info is None:
            return Ref(surface, kind, "not_found", reason=f"no {REF_KINDS[kind][0].lower()} named {name} here")
        locs = []
        if info.get("path"):
            path = info["path"]
            rel = os.path.relpath(path, self.root) if path.startswith(self.root + os.sep) else path
            locs.append({"role": "definition", "path": rel, "line": 1, "resolver": "definitions"})
        return Ref(surface, kind, "resolved", name, data={**info, "phrase": False}, locations=locs)

    def _job_reanchor(self, arg, _meta):
        surface, index, line = arg
        ref, st = self._load(surface), self._repo_state()
        if ref is None or index >= len(ref.locations):
            return
        ref.locations[index]["line"] = line
        self._anchor(ref.locations[index], st)
        if ref.status == "stale":
            ref.status = "resolved"
        self._save(ref, st["fp"])
        self._open().commit()
        self._post([ref])

    def _job_stale(self, surface, _meta):
        ref = self._load(surface)
        if ref is not None and ref.status != "stale":
            ref.status = "stale"
            self._save(ref, ref.fingerprint)
            self._open().commit()
            self._post([ref])

    def _job_lsp(self, on, _meta):
        if not on:
            if self.code is not None:
                self.code.stop()
            self._tell(entry="Language servers off: code names are found by search")
        elif self._code() is not None:  # there already (else _code fetches them, then looks again)
            GLib.idle_add(self._lsp_ready)

    def _code(self):
        """The language servers, if Signal may use them and they're installed (worker thread); else None, and
        the first time one is wanted, the window asks once whether to fetch them (or, if you said yes, they're
        fetched). Without them, code names are found by search."""
        on = load_settings().get("signal_lsp")
        if on is False:
            return None
        if self.code is None:
            self.code = RefCode(self)
        if self.code.ready():
            return self.code
        if self.halted:  # (looking up here alone: what's installed only, nothing fetched or asked)
            return None
        if not lsp_node():
            if "no node" not in self.lsp_said:
                self.lsp_said.add("no node")
                self._tell(entry="Language servers need Node.js, which isn't installed: code names are found by search")
        elif on is None and "asked" not in self.lsp_said:
            self.lsp_said.add("asked")
            GLib.idle_add(self._ask_lsp)
        elif on and "fetch" not in self.lsp_said:
            self.lsp_said.add("fetch")
            threading.Thread(target=self._fetch_lsp, daemon=True, name="baude-lsp-fetch").start()
        return None

    def _fetch_lsp(self):
        self._tell(entry="Fetching the language servers: " + ", ".join(f"{n} {v}" for n, v in REF_LSP_PACKAGES)
                         + f" into {pretty_path(lsp_dir())}")
        t0 = time.monotonic()
        ok, what = lsp_fetch()
        self._tell(entry=f"Language servers: {what} ({time.monotonic() - t0:.0f} s)")
        if ok:
            if self.code is not None:
                self.code.checked = 0
            GLib.idle_add(self._lsp_ready)

    def _validate(self, ref, st, meta):
        """A known name, mentioned again: move its locations with the code, or mark it stale."""
        if ref.kind == "branch":  # a branch moves on: look it up again when the repository has
            return ref if ref.fingerprint == st["fp"] else self._resolve(ref.surface, "branch", st, meta)
        if ref.kind not in ("file", "env", "constant", "route", "k8s", "config"):
            return ref
        lost = False
        for loc in ref.locations:
            _full, line, how = self.locate(loc)
            if how == "moved":
                loc["line"] = line
                self._anchor(loc, st)
            lost = lost or how == "lost"
        if not lost:
            if ref.status == "stale":
                ref.status = "resolved" if len(ref.locations) == 1 or ref.kind != "file" else "ambiguous"
            return ref
        cited = ref.kind == "file" and any(loc.get("line") for loc in ref.locations)
        if not cited:  # found again by name: the cheap tiers
            again = self._resolve(ref.surface, ref_kind(ref.surface) or ref.kind, st, meta)
            if again.status in ("resolved", "ambiguous"):
                return again
        ref.status = "stale"  # a cited line that's gone: keep it, so it can be opened as it was
        return ref

    def _resolve(self, surface, kind, st, meta, keep=True):
        """What a name points at (kind: what it looks like). keep False (looked up while Signal isn't running):
        nothing copied into .baude, and a tool's schema only if it's been fetched before."""
        if kind == "commit":
            ref = self._commit(surface)
        elif kind == "branch":
            ref = self._branch(surface) or self._file(surface, st, meta)
        elif kind == "pr":
            ref = self._pr(surface)
        elif kind == "env":
            ref = self._name(surface, st)
        elif kind == "route":
            ref = self._route(surface, st)
        elif kind == "cli":
            ref = self._cli(surface, st, meta)
        elif kind == "symbol":
            ref = self._symbol(surface, st, meta)
        elif kind == "k8s":
            ref = self._kube(surface, st, meta)
            if ref.status == "not_found" and "/" in surface and (ref_kube(surface) or ("",))[0] == "object":
                other = self._branch(surface) or self._file(surface, st, meta)  # (svc/app is a folder too)
                ref = other if other.status != "not_found" else ref
        elif kind == "config":
            ref = self._kube(surface, st, meta) if ref_kube(surface) else self._config(surface, surface, st) or \
                Ref(surface, "config", "not_found", reason="no YAML key or Helm helper has this name")
        elif kind == "tool":
            ref = self._tool(surface, fetch=keep)
        elif kind in ("subagent", "skill"):
            ref = self._definition(surface, kind, SUBAGENTS if kind == "subagent" else SKILLS)
        else:
            ref = self._file(surface, st, meta)
        if ref.status == "not_found" and kind in ("symbol", "env") and re.fullmatch(r"[A-Za-z_]\w{2,}", surface):
            ref = self._live_db_name(surface) or ref  # (built at run time: f"e_{entity}", say)
        for loc in ref.locations:
            self._anchor(loc, st, keep)
        ref.attempted = True
        return ref

    def _live_db_name(self, surface):
        """A Ref for a name no code defines that a running database has: a table or collection (or an entity's
        e_<surface> / a relation's r_<surface>), or a database. Clicked, it opens the schema diagram there."""
        try:
            hits = db_named(surface)
        except Exception:  # noqa: BLE001 (Docker or kubectl gone: nothing to say)
            return None
        if not hits:
            return None
        container, db, table = hits[0]
        servers = {s["container"]: s for s in pg_servers_cached()}
        engine = "ArangoDB" if (servers.get(container) or {}).get("engine") == "arango" else "Postgres"
        label = db_label(db)
        what = (f"{'Collection' if engine == 'ArangoDB' else 'Table'} {table} in {label}" if table else
                f"Database {label}") + f" ({engine}, {container})" + \
            (f", and {len(hits) - 1} more" if len(hits) > 1 else "")
        live = {"table": table, "database": db, "container": container, "engine": engine}
        return Ref(surface, "symbol", "resolved", table or db, data={"db": live, "note": what})

    def _anchor(self, loc, st, keep=True):
        """Record how a location's file looked when it was read: its hash, git state and the lines around it.
        A version that isn't committed is kept in .baude/blobs (unless the file may hold secrets, or keep is False:
        looked up while Signal isn't running). One on another branch (its rev) as that branch has it."""
        if loc.get("rev"):
            data = _git(self.top, "show", f"{loc['rev']}:{loc['path']}", raw=True) if self.top else None
            loc.update(state="branch", commit_sha=(_git(self.top, "rev-parse", loc["rev"]) or "").strip() or None,
                       branch=loc["rev"], resolved_at=time.time(), blob=blob_sha(data) if data else None,
                       snippet=None, snippet_start=None)
            if data and loc.get("line") and not REF_SENSITIVE.search(loc["path"]):
                lines = data.decode("utf-8", "replace").splitlines()
                start = max(1, loc["line"] - 3)
                loc["snippet"], loc["snippet_start"] = "\n".join(lines[start - 1:loc["line"] + 3]), start
            return
        full = os.path.join(self.root, loc["path"])
        xy = st["status"].get(loc["path"])
        state = ("committed" if xy is None else "untracked" if xy == "??" else
                 "unstaged" if xy[1] != " " else "staged") if self.top else "no git"
        loc.update(state=state, commit_sha=st["head"], branch=st["branch"], resolved_at=time.time(),
                   blob=None, snippet=None, snippet_start=None)
        if os.path.isdir(full):
            return
        try:
            with open(full, "rb") as f:
                data = f.read(self.BLOB_LIMIT + 1)
        except OSError:
            return
        if len(data) > self.BLOB_LIMIT:
            return
        loc["blob"] = blob_sha(data)
        sensitive = bool(REF_SENSITIVE.search(loc["path"]))
        if loc.get("line"):
            lines = data.decode("utf-8", "replace").splitlines()
            start = max(1, loc["line"] - 3)
            loc["snippet"] = "\n".join(ref_redact(s) if sensitive else s for s in lines[start - 1:loc["line"] + 3])
            loc["snippet_start"] = start
        if state != "committed" and not sensitive and keep:
            path = os.path.join(self.dir, "blobs", loc["blob"])
            if not os.path.exists(path):
                import zlib
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path + ".tmp", "wb") as f:
                    f.write(zlib.compress(data))
                os.replace(path + ".tmp", path)

    # ---- resolvers (worker thread): each returns a Ref; its locations are anchored afterwards
    def _file(self, surface, st, meta):
        m = re.fullmatch(r"(.*?)(?::(\d+)(?::\d+)?)?", surface)
        path, line = m.group(1), int(m.group(2)) if m.group(2) else None
        files, dirs, by_name = self._files(st)
        want = path[2:] if path.startswith("./") else path
        is_dir, want = want.endswith("/"), want.rstrip("/")
        expanded = os.path.expanduser(want)
        here = os.path.relpath(os.path.join(self.folder, want), self.root)
        if os.path.isabs(expanded):
            hits = [expanded]
        elif os.path.exists(os.path.join(self.root, want)):  # exactly that path: from the top, or the chat's folder
            hits = [os.path.normpath(want)]
        elif not here.startswith("..") and os.path.exists(os.path.join(self.root, here)):
            hits = [here]
        elif is_dir:
            hits = [d for d in dirs if d == want or d.endswith("/" + want)]
        elif "/" in want:
            hits = [f for f in files if f == want or f.endswith("/" + want)] or \
                   [d for d in dirs if d == want or d.endswith("/" + want)]
        else:  # a name: files with it, else folders with it (recipes.tsp/ is a folder)
            hits = list(by_name.get(want, [])) or [d for d in dirs if d.rsplit("/", 1)[-1] == want]
        if not hits and "/" in want and not want.startswith(".."):  # folders left out: applab-api/quick_edit.py
            parts = want.split("/")
            pool = dirs if is_dir else [f for f in files if f.rsplit("/", 1)[-1] == parts[-1]]
            hits = [f for f in pool if f.rsplit("/", 1)[-1] == parts[-1] and ref_in_order(parts[:-1], f.split("/")[:-1])]
        hits = sorted((h for h in hits if os.path.exists(os.path.join(self.root, h))), key=lambda h: (len(h), h))
        if len(hits) > 1:  # the one the session itself read or edited is the one it means
            touched = {os.path.relpath(p, self.root) for p in (meta or {}).get("touched", ())
                       if isinstance(p, str) and p.startswith(self.root + os.sep)}
            if len(mine := [h for h in hits if h in touched]) == 1:
                hits = mine
        if len(hits) > 1 and self.scope:  # else the one in the chat's package, if it has one (the package's first)
            mine = [h for h in hits if self._in_scope(h)]
            hits = mine if len(mine) == 1 else mine + [h for h in hits if h not in mine]
        if len(hits) > 1 and line:  # a cited line rules out the files too short to have it
            hits = [h for h in hits if self._line_count(h) >= line] or hits
        if not hits:
            where = os.path.basename(self.root) or self.root
            return Ref(surface, "file", "not_found", reason=f"no file or folder in {where} ends with {want} "
                                                           "(files git ignores, like build output, aren't searched)")
        locs = [{"role": "folder" if os.path.isdir(os.path.join(self.root, h)) else "file", "path": h,
                 "line": line, "resolver": "files"} for h in hits[:40]]
        if len(hits) == 1:
            return Ref(surface, "file", "resolved", hits[0] + (f":{line}" if line else ""), locations=locs)
        return Ref(surface, "file", "ambiguous", reason=f"{len(hits)} files end with {want}", locations=locs)

    def _line_count(self, path):
        try:
            with open(os.path.join(self.root, path), "rb") as f:
                return f.read(self.BLOB_LIMIT).count(b"\n") + 1
        except OSError:
            return 0

    def _commit(self, surface):
        """A commit (or a range of them: its last, and how many) in this repository, or in another the chat worked
        in (chat_repos)."""
        span = ref_commit_range(surface)
        tops = [t for t in (self.top, *self.chat_repos()) if t]
        if not tops:
            return Ref(surface, "commit", "not_found", reason="this folder isn't in a git repository")
        for top in tops:
            sha = (_git(top, "rev-parse", "--verify", "--quiet", f"{span[2] if span else surface}^{{commit}}")
                   or "").strip()
            base = (_git(top, "rev-parse", "--verify", "--quiet", f"{span[0]}^{{commit}}") or "").strip() \
                if span and sha else ""
            if sha and (base or not span):
                break
        else:
            return Ref(surface, "commit", "not_found",
                       reason="no commit with this hash in this repository or the others this chat worked in "
                              "(it may be another kind of hash)")
        github = self.github if top == self.top else github_repo(top)
        info = (_git(top, "show", "-s", "--format=%H%x00%an%x00%aI%x00%cI%x00%s%x00%P", sha) or "").rstrip("\n")
        full, author, adate, cdate, subject, parents = (info.split("\0") + [""] * 6)[:6]
        stat = _git(top, "diff", "--shortstat", base, sha) if base else \
            _git(top, "diff", "--shortstat", parents.split()[0], sha) if parents \
            else _git(top, "show", "--shortstat", "--format=", sha)
        num = lambda pat: int(m.group(1)) if (m := re.search(pat, stat or "")) else 0  # noqa: E731
        refs = (_git(top, "for-each-ref", "--contains", sha, "--format=%(refname)", "refs/heads", "refs/remotes")
                or "").split()
        branches = [r[len("refs/heads/"):] for r in refs if r.startswith("refs/heads/")]
        remotes = [r[len("refs/remotes/"):] for r in refs if r.startswith("refs/remotes/") and not r.endswith("/HEAD")]
        data = {"sha": full, "author": author, "date": adate, "committed": cdate, "subject": subject,
                "files": num(r"(\d+) files? changed"), "added": num(r"(\d+) insertions?"),
                "removed": num(r"(\d+) deletions?"), "branches": branches[:12], "remotes": remotes[:12],
                "pushed": bool(remotes)}
        if top != self.top:  # (another repository's: its own place, and its GitHub)
            data.update(repo=top, github=github)
        if span:
            data.update(range=base, count=int((_git(top, "rev-list", "--count", f"{base}..{sha}") or "0").strip()))
        if github and remotes:
            data["url"] = f"https://github.com/{github}/compare/{base}...{full}" if span else \
                f"https://github.com/{github}/commit/{full}"
        if not refs:  # on no branch: a rebase or squash replaced it. Its successor most likely kept the subject.
            data["rewritten"] = True
            for row in (_git(top, "log", "--all", "-F", f"--grep={subject}", "--format=%H%x00%s", "-n", "50")
                        or "").splitlines():
                c, _, s = row.partition("\0")
                if s == subject and c != full:
                    data["successor"] = c
                    break
        return Ref(surface, "commit", "resolved", f"{base}..{full}" if span else full, data=data)

    def _branch(self, surface):
        if not self.top:
            return None
        for ref in (f"refs/heads/{surface}", f"refs/remotes/{surface}"):
            if sha := (_git(self.top, "rev-parse", "--verify", "--quiet", ref) or "").strip():
                break
        else:
            return None
        remote = ref.startswith("refs/remotes/")
        name = surface.split("/", 1)[1] if remote else surface
        _sha, subject, date = ((_git(self.top, "log", "-1", "--format=%H%x00%s%x00%cI", ref) or "").strip().split("\0")
                               + ["", "", ""])[:3]
        upstream = "" if remote else (_git(self.top, "rev-parse", "--abbrev-ref", f"{surface}@{{upstream}}") or "").strip()
        base = upstream or (_git(self.top, "symbolic-ref", "--short", "refs/remotes/origin/HEAD") or "").strip()
        data = {"ref": ref, "name": name, "sha": sha, "subject": subject, "date": date, "upstream": upstream,
                "base": base, "pushed": remote or bool(_git(self.top, "rev-parse", "--verify", "--quiet",
                                                             f"refs/remotes/origin/{name}"))}
        if base and base != surface:
            counts = (_git(self.top, "rev-list", "--left-right", "--count", f"{base}...{ref}") or "").split()
            if len(counts) == 2:
                data["behind"], data["ahead"] = int(counts[0]), int(counts[1])
        if self.github and data["pushed"]:
            data["url"] = f"https://github.com/{self.github}/tree/{name}"
        return Ref(surface, "branch", "resolved", ref, data=data)

    def _pr(self, surface):
        if (self.catalogue.get(surface) or {}).get("kind") == "branch" and (ref := self._branch(surface)) is not None:
            return ref  # (a branch may be called feature/login#12)
        m = re.search(r"(?:([\w.-]+/[\w.-]+))?#(\d+)$", surface)
        repo = (m.group(1) if m else None) or self.github
        if not m or not repo:
            return Ref(surface, "pr", "not_found", reason="no GitHub repository to look it up in")
        n = int(m.group(2))
        data = {"number": n, "repo": repo, "url": f"https://github.com/{repo}/pull/{n}"}
        if (listed := GITHUB.known(repo, n)) is not None:  # (read already: what it is, and where it stands)
            data.update(github_data(listed))
        return Ref(surface, "pr", "resolved", f"{repo}#{n}", data=data)

    def _needs_github(self, ref):
        if ref.kind not in ("commit", "branch", "pr") or ref.status != "resolved" or not shutil.which("gh") or \
                ref.data.get("issue"):  # (an issue: no pull request's details to read)
            return False
        if ref.kind != "pr" and not ((ref.data.get("github") or self.github) and ref.data.get("pushed")):
            return False
        return time.time() - (ref.data.get("fetched_at") or 0) > self.GITHUB_TTL

    def _enrich(self, ref):
        """Pull requests from GitHub: a commit's, a branch's, or a PR's own details."""
        d = ref.data
        d["fetched_at"] = time.time()
        if ref.kind == "commit":
            prs = _gh_json("api", f"repos/{d.get('github') or self.github}/commits/{d['sha']}/pulls")
            if isinstance(prs, list):
                d["prs"] = [ref_pr_info(p) for p in prs[:10]]
        elif ref.kind == "branch":
            prs = _gh_json("pr", "list", "--repo", self.github, "--head", d["name"], "--state", "all", "--limit", "10",
                           "--json", "number,title,state,createdAt,mergedAt,closedAt,url")
            if isinstance(prs, list):
                d["prs"] = [{"number": p.get("number"), "title": p.get("title", ""), "state": p.get("state", "").lower(),
                             "url": p.get("url"), "created": p.get("createdAt"), "merged": p.get("mergedAt"),
                             "closed": p.get("closedAt")} for p in prs]
        else:
            p = _gh_json("api", f"repos/{d['repo']}/pulls/{d['number']}")
            if isinstance(p, dict) and p.get("number"):
                d.update(ref_pr_info(p), additions=p.get("additions"), deletions=p.get("deletions"),
                         changed_files=p.get("changed_files"), author=(p.get("user") or {}).get("login"),
                         head=(p.get("head") or {}).get("ref"), base=(p.get("base") or {}).get("ref"))
                return f"{ref.surface}: {d.get('title', '')} ({d.get('state', '')})"
            return f"no answer about {ref.surface}"
        if "prs" not in d:
            return f"no answer about {ref.surface} (offline, or gh isn't signed in)"
        return f"{len(d['prs'])} pull request{'s' * (len(d['prs']) != 1)} for {ref.surface}"

    def _env_prefixes(self, st):
        """pydantic settings classes' env_prefix: their fields are read from PREFIX_FIELD, a name no
        search for the env var itself finds."""
        def build():
            return [(path, m.group(1)) for path, _n, text in
                    self._grep(["-E", r"env_prefix[[:space:]]*=[[:space:]]*[\"'][A-Za-z0-9_]+[\"']"], ["*.py"])
                    if (m := re.search(r"env_prefix\s*=\s*[\"']([A-Za-z0-9_]+)[\"']", text))]
        return self._index("env_prefixes", st, build)

    def _name(self, name, st):
        """An UPPER_SNAKE name: an env var if something sets or reads it, else a constant if it's defined."""
        hits = [(p, n, ref_name_role(p, t, name), "") for p, n, t in self._grep(["-w", "-F", "-e", name])]
        for path, prefix in self._env_prefixes(st):
            if name.upper().startswith(prefix.upper()) and len(name) > len(prefix):
                field = name[len(prefix):].lower()
                try:
                    with open(os.path.join(self.root, path), encoding="utf-8", errors="replace") as f:
                        for n, text in enumerate(f, 1):
                            if re.match(rf"\s*{re.escape(field)}\s*[:=]", text):
                                hits.append((path, n, "read", f"settings field {field} (env_prefix {prefix})"))
                except OSError:
                    pass
        order = ["read", "set", "secret source", "defined", "used", "doc"]
        caps = {"secret source": 10, "set": 20, "read": 20, "defined": 10, "used": 10, "doc": 3}
        code = (".py", ".pyi", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".rs", ".go")
        locs, seen, taken = [], set(), {}
        for path, n, role, note in sorted(hits, key=lambda h: (order.index(h[2]), not h[0].endswith(code),
                                                                ref_is_test(h[0]), h[0], h[1])):
            if (path, n) in seen or taken.get(role, 0) >= caps[role]:
                continue
            seen.add((path, n))
            taken[role] = taken.get(role, 0) + 1
            locs.append({"role": role, "path": path, "line": n, "note": note, "resolver": "grep"})
        roles = {loc["role"] for loc in locs}
        if roles & {"secret source", "set", "read"}:
            return Ref(name, "env", "resolved", name, locations=locs)
        defs = [loc for loc in locs if loc["role"] == "defined"]
        if defs:
            canonical = f"{defs[0]['path']}::{name}" if len(defs) == 1 else name
            return Ref(name, "constant", "resolved", canonical, locations=locs)
        if locs:
            return Ref(name, "env", "not_found", locations=locs,
                       reason=f"only mentioned ({len(hits)} places): nothing sets, reads or defines it")
        return Ref(name, "env", "not_found", reason=f"not in any file in {os.path.basename(self.root) or self.root}")

    def _symbol(self, surface, st, meta):
        """A code name: its definitions (def, class, function, const, interface, a field, a table or column).
        module.name looks in that module first; obj.attr, with no module of that name, lists where it's used
        and the attr fields defined there. Which class obj is takes a language server (_symbol_lsp): its answer,
        when it has one, comes first. A span that's more than a name (ref_span) has resolvers of its own."""
        if span := ref_span(surface):
            return self._span(surface, span, st, meta)
        name = re.sub(r"\[[^\]]*\]", "", re.sub(r"\([^()]*\)$", "", surface))
        parts = name.split(".")
        last = parts[-1]
        words = self._grep(["-w", "-F", "-e", last], REF_CODE_PATHS, limit=30000)  # one fast search, then
        defs = [(p, line, role) for p, line, text in words if (role := self._def_role(p, line, text, last))]
        if surface.endswith(")"):  # a call: only what can be called
            defs = [d for d in defs if d[2] in ("function", "method", "class", "variable", "re-export")]
        defs = [d for d in defs if not ref_is_test(d[0])] or defs  # tests only when nothing else defines it
        stem = lambda p: os.path.splitext(os.path.basename(p))[0]  # noqa: E731
        touched = self._touched(meta)
        loc = lambda p, line, role, note="": {"role": role, "path": p, "line": line, "resolver": "grep",  # noqa: E731
                                              "note": note}
        def modules():  # a module or package of that name: export_openapi, orchard_relay.gen
            files, dirs, _by_name = self._files(st)
            code = tuple(p[1:] for p in REF_CODE_PATHS)
            found = [(f, "module") for f in files if f.endswith(code) and stem(f) == last] + \
                    [(d, "folder") for d in dirs if d.rsplit("/", 1)[-1] == last]
            return [loc(p, None, role) for p, role in found if ref_in_order(parts[:-1], p.split("/")[:-1])]
        def dotted():  # the patterns' answer for module.name and obj.attr
            mod = parts[-2]
            mine = [d for d in defs if stem(d[0]) == mod or os.path.basename(os.path.dirname(d[0])) == mod]
            if mine:
                return self._symbol_ref(surface, name, [loc(*d) for d in mine], touched)
            if mods := modules():
                return self._symbol_ref(surface, name, mods, touched)
            used = [(p, line) for p, line, _t in self._grep(["-F", "-e", name], REF_CODE_PATHS)]
            files = {p for p, _ in used}
            members = [d for d in defs if d[2] in ("field", "attribute", "method", "column")]
            if of := self._of_class(members, mod):  # sr.dlg.quick_edits: Dlg's, whatever sr is
                return self._symbol_ref(surface, name, [loc(p, line, role, f"{cls}.{last}") for p, line, role, cls in of]
                                        + [loc(p, line, "used") for p, line in used[:8]], touched)
            near = [d for d in members if d[0] in files] or \
                   [d for d in members if any(os.path.dirname(d[0]) == os.path.dirname(f) for f in files)]
            locs = [loc(p, line, "used") for p, line in used[:8]] + \
                   [loc(p, line, role, f"{last}") for p, line, role in (near or members)[:12]]
            if used:
                return Ref(surface, "symbol", "resolved", name, locations=locs)
            if members:
                return self._symbol_ref(surface, name, locs, touched)
            if conf := self._config(surface, name, st):
                return conf
            return Ref(surface, "symbol", "not_found",
                       reason=f"no module {mod} defines {last}, and {name} isn't used in any code file")
        if len(parts) >= 2:
            ref = dotted()
            if (code := self._code()) is None or (lsp := self._symbol_lsp(surface, name, parts, st, touched, code)) is None:
                return ref
            if lsp.kind == "symbol":  # the server's definition first, then what the patterns had (other matches)
                have = {(l["path"], l["line"]) for l in lsp.locations}
                lsp.locations += [l for l in ref.locations if (l["path"], l["line"]) not in have][:30 - len(lsp.locations)]
            return lsp
        ents = self._entity_fields(last) if "_" in last and not surface.endswith(")") else []
        if defs or ents:  # (a data model's fields and columns come first: the schema the code's models mirror)
            return self._symbol_ref(surface, name, [{**loc(p, line, "entity field", note), "resolver": "yaml"}
                                                    for p, line, note in ents] + [loc(*d) for d in defs], touched)
        if mods := modules():
            return self._symbol_ref(surface, name, mods, touched)
        if words and (code := self._code()) is not None and (ref := self._lsp_bare(surface, name, words, st, touched, code)):
            return ref
        if conf := self._config(surface, name, st):
            return conf
        used = words
        return Ref(surface, "symbol", "not_found", locations=[loc(p, line, "used") for p, line, _t in used[:8]],
                   reason=f"used in {len(used)} places but defined in no code file (a JSON field, a config key?)"
                   if used else "not in any code file")

    def _touched(self, meta):
        """The files the session's own tools read or wrote, relative to the repository."""
        return {os.path.relpath(t, self.root) for t in (meta or {}).get("touched", ())
                if isinstance(t, str) and t.startswith(self.root + os.sep)}

    # ---- spans that are more than a name (ref_span): each is one glossary entry, its words looked up together
    def _span(self, surface, span, st, meta):
        form, a, b = span
        touched = self._touched(meta)
        if form == "shape":
            return self._shape(surface, a, b, touched)
        if form == "pair":
            return self._pair(surface, a, b, touched)
        if form == "query":
            return self._query(surface, a, touched)
        return self._excerpt(surface, a, touched)

    def _span_ref(self, surface, locs, touched, missing):
        """The places found for a span, best first: one is found; several are, the one in a file the session
        touched first; none, it's not found for the reason given. Tests count only when nothing else has it."""
        locs = ([l for l in locs if not ref_is_test(l["path"])] or locs)[:30]
        if not locs:
            return Ref(surface, "symbol", "not_found", reason=missing)
        if len(locs) > 1 and len(mine := [l for l in locs if l["path"] in touched]) == 1:
            locs = mine + [l for l in locs if l is not mine[0]]
            return Ref(surface, "symbol", "resolved", f"{mine[0]['path']}::{surface}", locations=locs)
        if len(locs) == 1:
            return Ref(surface, "symbol", "resolved", f"{locs[0]['path']}::{surface}", locations=locs)
        return Ref(surface, "symbol", "ambiguous", surface, reason=f"{len(locs)} places", locations=locs)

    def _shape(self, surface, key, names, touched):
        """An object's shape, {installation_id, member_id, name}: the class, type, table or data-model entity that
        declares all those names, else an object or a parameter list that does. With a key, previous: {job_id,
        seq}: the key's definition, typed with such a class or near where code writes the key with that shape."""
        paths = REF_CODE_PATHS + ["*.yaml", "*.yml"]
        files = None
        for n in sorted(names, key=len, reverse=True):  # (the longest names are the rarest: the fewest files)
            got = self._grep_files(["-w", "-F", "-e", n], paths)
            files = got if files is None else files & got
            if not files:
                break
        tiers = {"class": 0, "type": 0, "table": 0, "entity": 0, "object": 1, "parameters": 2}
        found = []
        for path in sorted(files or ())[:3000]:
            lines = self._text(path).splitlines()
            for h, extra in ref_shape_blocks(lines, names):
                role, cname = ref_container(lines, h, path)
                found.append({"role": role, "path": path, "line": h + 1, "resolver": "shape", "name": cname,
                              "extra": extra, "note": f"{cname}: {', '.join(names)}" if cname else ", ".join(names)})
        # the declared types first, and among them the ones with the fewest other members: the shape as written
        found.sort(key=lambda l: (tiers[l["role"]], ref_is_test(l["path"]), l["extra"], l["path"] not in touched,
                                  l["path"], l["line"]))
        what = "{" + ", ".join(names) + "}"
        if key is None:
            top = [l for l in found if not ref_is_test(l["path"])] or found
            best = [l for l in top if (tiers[l["role"]], l["extra"]) == (tiers[top[0]["role"]], top[0]["extra"])]
            return self._span_ref(surface, [{k: v for k, v in l.items() if k not in ("name", "extra")} for l in best],
                                  touched, f"nothing declares all of {what}")
        last = key.split(".")[-1]
        opens = re.compile(rf"(?<![\w.]){re.escape(last)}[\"']?\??\s*[:=]\s*\{{")
        written = [l for l in found if l["role"] == "object" and opens.search(self._text(l["path"]).splitlines()[l["line"] - 1])]
        classes = {l["name"] for l in found if l["role"] in ("class", "type") and l["name"]}
        defs = [(p, n, role, t) for p, n, t in self._grep(["-w", "-F", "-e", last], paths, limit=30000)
                if (role := self._def_role(p, n, t, last)) in ("field", "column", "attribute")]
        typed = [d for d in defs if any(re.search(rf"\b{re.escape(c)}\b", d[3]) for c in classes)]
        here = {l["path"] for l in written}
        near = [d for d in defs if d[0] in here] or [d for d in defs if os.path.dirname(d[0]) in {os.path.dirname(p) for p in here}]
        pick = typed or near
        pick = [d for d in pick if any(re.search(rf"\b{re.escape(n)}\b", d[3]) for n in names)] or pick  # (its comment)
        locs = [{"role": role, "path": p, "line": n, "resolver": "shape", "note": f"{last}: {what}"}
                for p, n, role, _t in pick] + \
               [{"role": "used", "path": l["path"], "line": l["line"], "resolver": "shape", "note": l["note"]} for l in written]
        ref = self._span_ref(surface, locs, touched, f"nothing defines {key} as {what}")
        if ref.status == "ambiguous" and sum(l["role"] != "used" for l in ref.locations) == 1:
            ref.status, ref.canonical = "resolved", f"{ref.locations[0]['path']}::{surface}"  # (one definition, used here)
        return ref

    def _pair(self, surface, key, value, touched):
        """key: value, preview.reason: crashed or origin: quick_edit: the key's definitions that name the value, on
        their line (reason: Literal["crashed"]) or where the type they're declared with is (origin: Origin, and
        Origin = Literal[..., "quick_edit", ...]); in YAML, the key whose line or block has it (strategy: then
        type: Recreate). When none does, it isn't found: every definition of a plain word like origin isn't it."""
        last = key.split(".")[-1]
        has = re.compile(rf"(?<![\w-]){re.escape(value)}(?![\w-])")
        hits, types = [], {}
        for p, n, t in self._grep(["-w", "-F", "-e", last], REF_CODE_PATHS + ["*.yaml", "*.yml"], limit=30000):
            if p.endswith((".yaml", ".yml")):
                if m := re.match(rf"(\s*)-?\s*{re.escape(last)}\s*:(.*)", t):
                    lines = self._text(p).splitlines()
                    block = [m.group(2)] + list(itertools.takewhile(
                        lambda s: not s.strip() or len(s) - len(s.lstrip()) > len(m.group(1)), lines[n:n + 12]))
                    if any(has.search(s.split("#")[0]) for s in block):
                        hits.append({"role": "key", "path": p, "line": n, "resolver": "yaml", "note": f"{last}: {value}"})
                continue
            if self._def_role(p, n, t, last) not in ("field", "column", "attribute", "variable"):
                continue
            after = t.split(last, 1)[1]
            if has.search(after.split("#")[0].split("//")[0]):
                hits.append({"role": "field", "path": p, "line": n, "resolver": "grep", "note": f"{last}: {value}"})
            else:
                for typ in re.findall(r"\b([A-Z]\w+)\b", after.split("=")[0]):
                    if typ not in _REF_TYPING:
                        types.setdefault(typ, (p, n))
        for typ in list(types)[:8] if not hits else ():  # origin: Origin, where Origin names the value
            for p, n, t in self._grep(["-w", "-F", "-e", typ], REF_CODE_PATHS, limit=2000):
                if self._def_role(p, n, t, typ) in ("variable", "type", "class"):
                    lines = self._text(p).splitlines()
                    body = [t] + list(itertools.takewhile(lambda s: not s.strip() or s[:1].isspace(), lines[n:n + 40]))
                    if any(has.search(s) for s in body):
                        hits.append({"role": "type", "path": p, "line": n, "resolver": "grep",
                                     "note": f"{typ}, the type of {last}"})
        return self._span_ref(surface, hits, touched, f"no definition of {last} names {value}")

    def _query(self, surface, name, touched):
        """?name=value: where code reads that query parameter (searchParams.get("name"), request.args["name"],
        Query(alias="name"), a FastAPI parameter name: str = Query(...))."""
        q = re.escape(name)
        reads = re.compile(rf"(?:params|query|args|search\w*|URLSearchParams\([^)]*\))\.(?:get|getAll|has)\(\s*[\"']{q}[\"']"
                           rf"|(?:params|query|args|query_params)\s*\[\s*[\"']{q}[\"']|alias\s*=\s*[\"']{q}[\"']"
                           rf"|\b{q}\s*:[^=]*=\s*Query\(", re.I)
        locs = [{"role": "reads", "path": p, "line": n, "resolver": "grep", "note": f"?{name}="}
                for p, n, t in self._grep(["-F", "-e", name], REF_CODE_PATHS, limit=20000) if reads.search(t)]
        return self._span_ref(surface, locs, touched, f"no code reads the query parameter {name}")

    def _excerpt(self, surface, text, touched):
        """A piece of code, keep_dist=package == "orchard-ui": where it's written (any spacing, either quote, an
        ellipsis for anything). In more than five places it's too common to point anywhere."""
        frags = [f for f in re.split(r"\s+|\.\.\.|…|[\"'`]", text) if len(f) >= 3]
        if not frags:
            return Ref(surface, "symbol", "not_found", reason="too short to look for")
        pattern = ref_excerpt_re(text)
        paths = REF_CODE_PATHS + ["*.yaml", "*.yml", "*.toml", "*.json", "*.css", "*.scss", "*.html", "*.sh", "*.tsp"]
        hits = [{"role": "written", "path": p, "line": n, "resolver": "grep", "note": ""}
                for p, n, t in self._grep(["-F", "-e", max(frags, key=len)], paths, limit=5000)
                if not REF_SENSITIVE.search(p) and pattern.search(t)]
        hits = [h for h in hits if not ref_is_test(h["path"])] or hits
        if len(hits) > 5:
            return Ref(surface, "symbol", "not_found", reason=f"written in {len(hits)} places: too common to point at one")
        return self._span_ref(surface, hits, touched, "not written in any code file")

    def _config(self, surface, name, st=None):
        """A name that isn't code but configuration: a Helm helper ({{ define "app.previewUpstream" }}) or a
        YAML key, matched by its dotted path (livePreview.runner.previewPort), with the templates using it. A path a
        Helm chart's values files have from their top is that chart's value (_kube_values)."""
        parts, last = name.split("."), name.split(".")[-1]
        if st is not None and len(parts) > 1 and self._kube_index(st)["charts"]:
            ref = self._kube_values(surface, name, st, fallback=False)
            if ref is not None:
                return ref
        texts, locs = {}, []

        def lines(path):
            if path not in texts:
                try:
                    with open(os.path.join(self.root, path), encoding="utf-8", errors="replace") as f:
                        texts[path] = f.read().splitlines()
                except OSError:
                    texts[path] = []
            return texts[path]
        yaml = ["*.yaml", "*.yml", "*.tpl"]
        for path, n, text in self._grep(["-w", "-F", "-e", last], yaml, limit=20000):
            if re.search(rf"define\s+\"{re.escape(name)}\"", text):
                locs.append({"role": "helm helper", "path": path, "line": n, "resolver": "yaml", "note": ""})
            elif re.match(rf"\s*-?\s*{re.escape(last)}\s*:", text) and \
                    ref_in_order(parts[:-1], ref_yaml_path(lines(path), n - 1)[:-1]):
                key = ".".join(ref_yaml_path(lines(path), n - 1))
                locs.append({"role": "key", "path": path, "line": n, "resolver": "yaml", "note": key})
        if len(parts) > 1:  # where templates read it: .Values.<name>, include "<name>"
            for path, n, _t in self._grep(["-F", "-e", f".Values.{name}", "-e", f'include "{name}"'], yaml)[:10]:
                locs.append({"role": "used", "path": path, "line": n, "resolver": "yaml", "note": ""})
        defs = [l for l in locs if l["role"] != "used"]
        if not defs:
            return None
        locs = locs[:30]
        if len(defs) == 1:
            return Ref(surface, "config", "resolved", f"{defs[0]['path']}::{name}", locations=locs)
        return Ref(surface, "config", "ambiguous", name, reason=f"{len(defs)} keys named {last}", locations=locs)

    def _def_role(self, path, line, text, name):
        """ref_def_role, plus a table named on the line after create_table( / Table( (alembic's layout)."""
        if role := ref_def_role(path, text, name):
            return role
        if re.fullmatch(rf"\s*[\"']{re.escape(name)}[\"'],?\s*", text) and line > 1:
            try:
                with open(os.path.join(self.root, path), encoding="utf-8", errors="replace") as f:
                    before = f.read().splitlines()[line - 2]
            except (OSError, IndexError):
                return None
            if re.search(r"(create_table|\bTable)\(\s*$", before):
                return "table"
            if re.search(r"(Column|add_column\([^,]*,\s*\w*\.?Column)\(\s*$", before):
                return "column"
        return None

    def _in_scope(self, path):
        """Whether a path (relative to the checkout) is in this Signal's package (always, with none)."""
        return not self.scope or path == self.scope or path.startswith(self.scope + "/")

    def _symbol_ref(self, surface, name, locs, touched):
        """One definition: found. Several: the one in a file the session touched, else the one in the chat's package
        (this Signal's scope), else all of them, the package's first."""
        schema = ("entity field", "table", "column")  # (a data model's own definitions before the code's copies)
        locs = sorted(locs, key=lambda l: (l["role"] == "used", l["role"] not in schema, not self._in_scope(l["path"]),
                                           ref_is_test(l["path"]), l["path"], l["line"] or 0))[:30]
        defs = [l for l in locs if l["role"] != "used"]
        if len(defs) > 1 and len(mine := [l for l in defs if l["path"] in touched]) == 1:
            locs = mine + [l for l in locs if l is not mine[0]]
            defs = mine
        if len(defs) > 1 and self.scope and len(mine := [l for l in defs if self._in_scope(l["path"])]) == 1:
            locs = mine + [l for l in locs if l is not mine[0]]
            defs = mine
        if len(defs) == 1:
            return Ref(surface, "symbol", "resolved", f"{defs[0]['path']}::{name}", locations=locs)
        return Ref(surface, "symbol", "ambiguous", name, reason=f"{len(defs)} definitions of {name}", locations=locs)

    def _entity_fields(self, name):
        """A data model's fields named so, declared in YAML (a Widget's system.yaml: ontology, entities, each one's
        fields): [(path, line, "Entity.name")]."""
        out = []
        for path, n, text in self._grep(["-w", "-F", "-e", name], ["*.yaml", "*.yml"], limit=5000):
            if not re.match(rf"\s+{re.escape(name)}\s*:", text):
                continue
            lines = self._text(path).splitlines()
            keys = ref_yaml_path(lines, n - 1)
            if len(keys) >= 3 and keys[-2] == "fields" and "entities" in keys:
                indent = len(text) - len(text.lstrip())
                entity = next((m.group(1) for s in reversed(lines[:n - 1]) if len(s) - len(s.lstrip()) < indent
                               and (m := re.match(r"\s*-\s*name:\s*[\"']?([\w.-]+)", s))), None)
                out.append((path, n, f"{entity}.{name}" if entity else name))
        return out

    def _of_class(self, members, obj):
        """The members (path, line, role) that are in a class named like the object they're used on: in
        sr.dlg.quick_edits, Dlg's quick_edits, whatever sr is. Python only (its own parser): [(path, line, role, class)]."""
        out = []
        for path, line, role in members[:60]:
            if path.endswith((".py", ".pyi")) and (classes := ref_py_scope(self._text(path), line)[1]) \
                    and ref_same_name(obj, classes[-1]):
                out.append((path, line, role, classes[-1]))
        return out

    # ---- through the language servers (RefCode): the names a search can't settle
    def _symbol_lsp(self, surface, name, parts, st, touched, code):
        """A dotted name, through the language servers; None if they don't know it. First the definition they give
        where code uses the name (previous.job_id: whatever previous is there); then, for Class.member.member, each
        member in its class, going on through its type (ShellRelay.tools.invoke: tools is a ToolConsumer). A name
        defined outside the repository (the standard library, a dependency) is external."""
        used = self._grep(["-F", "-e", name], REF_CODE_PATHS, limit=3000)
        found, outside = self._lsp_definitions(code, ref_code_sites(used, name, touched), name, parts[-1], st)
        how = "where the code uses it"
        if not found and parts[0][:1].isupper():
            found, how = self._lsp_chain(code, parts, st), f"through the members of {parts[0]}"
        return self._lsp_ref(surface, name, parts[-1], found, outside, used, how)

    def _lsp_bare(self, surface, name, words, st, touched, code):
        """A name no pattern found a definition of: the one a language server gives where code uses it (a field
        assigned in a class body, a name from a dependency), else a symbol of that name in the packages using it."""
        sites = ref_code_sites(words, name, touched)
        found, outside = self._lsp_definitions(code, sites, name, name, st)
        how = "where the code uses it"
        if not found and not outside:  # (a cold package's symbols take a while: only the first package's)
            for path in [s[0] for s in sites][:1]:
                for p, n, _kind, _container in code.workspace(path, name):
                    if (place := self._lsp_place(p, n, st)) and not place[2] and self._lsp_role(*place[:2], name) \
                            and place[:2] not in found:
                        found.append(place[:2])
            how = "among the symbols of the packages that use it"
        return self._lsp_ref(surface, name, name, found, outside, words, how)

    def _lsp_definitions(self, code, sites, needle, last, st):
        """The definitions the servers give for last, the end of needle, at the first site they answer for: the
        repository's ([(path, line)]), or those outside it ([(path, line, what it is)]). Sites are tried in turn,
        starting at most two servers that aren't running yet (a package's server takes seconds to start)."""
        found, outside, new = [], [], 0
        for path, n, text, i in sites:
            if (key := code.key(path)) is None or key not in code.servers and new >= 2:
                continue
            new += key not in code.servers
            self._tell(activity=f"Asking a language server about {needle} ({path}:{n})…")
            for p, line in code.definitions(path, n, lsp_col(text, i + len(needle) - len(last))):
                if (place := self._lsp_place(p, line, st)) is None:
                    continue
                if place[2]:
                    outside.append(place)
                elif self._lsp_role(place[0], place[1], last) and place[:2] not in found:
                    found.append(place[:2])
            if found or outside:
                break
        return found, outside

    def _lsp_chain(self, code, parts, st):
        """Class.member.member: the class's definition, then each member in it, and the type of each member but the
        last (textDocument/typeDefinition: its class) to look in next. [(path, line)] of the last member."""
        heads = [(p, n) for p, n, t in self._grep(["-w", "-F", "-e", parts[0]], REF_CODE_PATHS, limit=5000)
                 if ref_def_role(p, t, parts[0]) in ("class", "type")]
        out = []
        for path, _line in sorted(heads, key=lambda h: ref_is_test(h[0]))[:3]:
            cls = parts[0]
            for i, part in enumerate(parts[1:], 1):
                member = next(((n, ch) for s, _k, n, ch, around in code.symbols(path)
                               if s == part and around and around[-1] == cls), None)
                if member is None:
                    break
                if i == len(parts) - 1:
                    if self._lsp_role(path, member[0], part) and (path, member[0]) not in out:
                        out.append((path, member[0]))
                    break
                types = [t for p, n in code.definitions(path, *member, method="textDocument/typeDefinition")
                         if (t := self._lsp_place(p, n, st)) and not t[2]]
                if not types:
                    break
                path, line = types[0][:2]
                cls = next((s for s, _k, n, _c, _a in code.symbols(path) if n == line), None)
                if cls is None:
                    break
        return out

    def _lsp_place(self, path, line, st):
        """Where a definition a language server gave is: (path relative to the repository, line, None), or for one
        outside it, (its path, line, what it is). A copy of one of the repository's own packages, installed in a
        virtual environment, maps back to the repository's file. None: nowhere worth a link."""
        real = os.path.realpath(path)
        rel = os.path.relpath(real, self.root) if ref_inside(real, self.root) else None
        if rel is not None and not re.search(r"(^|/)(site-packages|node_modules)/", rel):
            return rel, line, None
        if m := re.search(r"/site-packages/(.+)$", real):
            module = m.group(1)
            mine = sorted((f for f in self._files(st)[0] if f.endswith("/" + module) and "site-packages/" not in f), key=len)
            if mine:  # an installed copy of this repository's own package: the line with the same text in the original
                want = (self._lines_of(real)[line - 1:line] or [""])[0].strip()
                hits = [i for i, s in enumerate(self._text(mine[0]).splitlines(), 1) if want and s.strip() == want]
                return (mine[0], min(hits, key=lambda i: abs(i - line)), None) if hits else None
            return real, line, f"in the Python package {re.split(r'[/.-]', module)[0]}"
        if re.search(r"/typeshed[^/]*/stdlib/|/lib/python3[\d.]*/(?!site-packages/)", real):
            return real, line, "in the Python standard library"
        if m := re.search(r"/typeshed[^/]*/stubs/([^/]+)/", real):
            return real, line, f"in the Python package {m.group(1).removeprefix('types-')} (its type stubs)"
        if m := re.search(r"/node_modules/((?:@[^/]+/)?[^/]+)/", real):
            if m.group(1) in self._npm_names(st):
                return None  # this repository's own package, as built into node_modules: not its source
            return real, line, "in TypeScript's own types" if m.group(1) == "typescript" else \
                f"in the npm package {m.group(1)}"
        return real, line, "outside the repository"

    def _lines_of(self, full):
        try:
            with open(full, encoding="utf-8", errors="replace") as f:
                return f.read(self.BLOB_LIMIT).splitlines()
        except OSError:
            return []

    def _npm_names(self, st):
        """The names of the repository's own npm packages (each package.json's "name")."""
        def build():
            names = set()
            for f in self._files(st)[0]:
                if os.path.basename(f) == "package.json" and "node_modules/" not in f:
                    try:
                        names.add(json.loads(self._text(f)).get("name"))
                    except (ValueError, AttributeError):
                        pass
            return names - {None}
        return self._index("npm_names", st, build)

    def _lsp_role(self, path, line, name):
        """What a definition a language server gave is (a field, a method, a class attribute...), or None if it isn't
        worth a link: a local variable or a parameter, or a line without the name."""
        text = (self._text(path).splitlines()[line - 1:line] or [""])[0]
        if not re.search(rf"(?<![\w$]){re.escape(name)}(?![\w$])", text):
            return None
        if role := self._def_role(path, line, text, name):
            return role
        if path.endswith((".py", ".pyi")):
            return {"class": "attribute", "module": "variable"}.get(ref_py_scope(self._text(path), line)[0])
        return None

    def _lsp_ref(self, surface, name, last, found, outside, used, how):
        """The Ref for what the language servers found (None: nothing): the definitions, then where it's used."""
        if found:
            locs = [{"role": self._lsp_role(p, n, last) or "definition", "path": p, "line": n, "note": "",
                     "resolver": "lsp:pyright" if p.endswith((".py", ".pyi")) else "lsp:typescript"} for p, n in found[:12]]
            locs += [{"role": "used", "path": p, "line": n, "resolver": "grep", "note": ""}
                     for p, n, _t in used[:8] if (p, n) not in found]
            if len(found) == 1:
                ref = Ref(surface, "symbol", "resolved", f"{found[0][0]}::{name}", locations=locs)
            else:
                ref = Ref(surface, "symbol", "ambiguous", name, locations=locs,
                          reason=f"{len(found)} definitions, by a language server {how}")
            ref.data["lsp"] = how
            return ref
        if outside:
            path, line, what = outside[0]
            return Ref(surface, "external", "resolved", name, data={"note": f"Defined {what}", "lsp": how},
                       locations=[{"role": "definition", "path": path, "line": line, "note": "",
                                   "resolver": "lsp:pyright" if path.endswith((".py", ".pyi")) else "lsp:typescript"}])
        return None

    def _route(self, surface, st):
        m = re.fullmatch(rf"(?:({_REF_METHODS})\s+)?(…|\.\.\.)?(/\S*?)(…|\.\.\.)?", surface)
        if not m:
            return Ref(surface, "route", "not_found", reason="not a route")
        method, tail, target = m.group(1), bool(m.group(2)), m.group(3).split("?")[0]
        prefix_only = target.endswith("/*") or bool(m.group(4)) and not tail  # (/v1/private/jobs/…: what's under it)
        target = (target[:-2] if target.endswith("/*") else target).rstrip("/") or "/"
        idx = self._index("routes", st, lambda: self._build_routes(st))
        handlers = [r for r in idx["handlers"] if not (method and r["methods"] and method not in r["methods"])]

        def best_of(score):  # (score, handler): only the most specific count; catch-alls never do
            found = []
            for r in handlers:
                scores = [x for p in r["paths"] if (x := score(p)) is not None]
                if scores:  # a handler for exactly this method beats one for any method
                    found.append((max(scores), bool(method) and method in r["methods"], r))
            top = max(((f[0], f[1]) for f in found), default=None)
            return [(r, p) for x, exact, r in found if (x, exact) == top and x >= 0
                    for p in sorted(r["paths"])[:1]], top

        written = target.strip("/").split("/")
        literal = sum(1 for g in written if not re.fullmatch(r"\{[^}]*\}|<[^>]*>|:\w+|\*", g))
        if written and re.fullmatch(r"\{[^}]*\}|<[^>]*>|:\w+", written[0]):
            tail = True  # (/{job}/edits: under a prefix the reply named before; /{job} alone can't be placed)

        def suffix(p):  # the end of a route, as replies often write it: /conversations/{id}/browser/{call_id}, …/progress
            t = p.strip("/").split("/")
            if len(written) < (1 if tail else 2) or len(t) <= len(written) or not literal:
                return None
            x = ref_route_score("/".join(t[-len(written):]), target)
            return x if x == literal else None  # every name written must match, not just parameters

        def under(p):  # a prefix (/v1/dlg, /v1/private/gen/*): every route below it
            return p.count("/") if p.startswith(target.rstrip("/") + "/") else None
        hits, top = ([], None) if prefix_only or tail else best_of(lambda p: ref_route_score(p, target))
        how = "handler"
        if not hits and not prefix_only and (hits := best_of(suffix)[0]):
            how = "ends the route"
        if tail and not hits:
            return Ref(surface, "route", "not_found", reason="no FastAPI route in this folder ends with it")
        if not hits and (hits := [(r, p) for r in handlers for p in sorted(r["paths"]) if under(p) is not None][:30]):
            how = "under this prefix"
        locs = []
        for r, full in hits:
            verbs = "/".join(sorted(r["methods"])) or "ANY"
            note = f"{verbs} {r['fn']}".strip() + ("" if how == "handler" else f": {full}")
            locs.append({"role": "handler", "path": r["path"], "line": r["line"], "resolver": "fastapi", "note": note})
        best = top
        for s in idx["specs"]:
            if (not method or method in s["methods"]) and ref_route_score(s["route"], target) is not None:
                locs.append({"role": "spec", "path": s["path"], "line": s["line"], "resolver": "openapi",
                             "note": f"{s['route']}"})
        for e in idx["edges"]:
            if target == e["prefix"] or (not e["exact"] and len(e["prefix"]) > 1 and target.startswith(e["prefix"])):
                locs.append({"role": "edge", "path": e["path"], "line": e["line"], "resolver": "nginx",
                             "note": f"location {'= ' if e['exact'] else ''}{e['prefix']}"})
        locs = locs[:30]
        if any(loc["role"] != "edge" for loc in locs):
            return Ref(surface, "route", "resolved", f"{method + ' ' if method else ''}{target}", locations=locs)
        catchall = " (only catch-all routes match it)" if best else ""
        return Ref(surface, "route", "not_found", locations=locs,
                   reason=f"no FastAPI route or OpenAPI path in this folder matches it{catchall}")

    def _cli(self, surface, st, meta):
        """A command or a flag: the module or script a command runs (python -m pkg.mod, node scripts/shot.mjs), the
        code that defines a flag (add_argument("--from"), click / typer options, argv checks)."""
        flag = surface if re.fullmatch(r"--[A-Za-z][\w-]*", surface) else None
        how = None if flag else ref_command(surface)
        program = None  # (mytool --check: the flag as mytool defines it, if this folder has it)
        if how and how[0] == "flag":
            flag, program = how[1], how[2] if len(how) > 2 else None
        if flag:
            fx = re.escape(flag)  # (a list that passes it to another program is a use, not its definition)
            defining = re.compile(r"add_argument|add_option|\.option\(|\b(?:Option|Argument)\(|(?:sys|process)\.argv|"
                                  rf"\bargv\.includes\(|getopt|^\s*[\"']?{fx}[\"']?(?:\|[^)]*)?\)")
            hits = self._grep(["-F", "-e", f'"{flag}', "-e", f"'{flag}", "-e", f"{flag})", "-e", f"{flag}|"],
                              REF_CODE_PATHS + ["*.sh", "*.bash"], limit=400)
            hits = [h for h in hits if re.search(rf"(?<![\w-]){fx}(?![\w-])", h[2])]
            if not hits:
                return Ref(surface, "cli", "not_found", reason=f"no code in this folder has the flag {flag}")
            texts = {}

            def defines(h):  # the line defines it, or it's "--from", in a call over several lines that does
                if defining.search(h[2]):
                    return True
                if not re.fullmatch(rf"\s*[\"']{fx}[\"'],?\s*", h[2]):
                    return False
                if h[0] not in texts:
                    try:
                        with open(os.path.join(self.root, h[0]), encoding="utf-8", errors="replace") as f:
                            texts[h[0]] = f.read(self.BLOB_LIMIT).splitlines()
                    except OSError:
                        texts[h[0]] = []
                return any(defining.search(l) for l in texts[h[0]][max(0, h[1] - 4):h[1] - 1])
            hits = [(h, defines(h)) for h in hits]
            mine = (lambda path: program.replace("-", "_").lower() not in path.replace("-", "_").lower()) if program \
                else (lambda path: False)
            hits.sort(key=lambda x: (not x[1], mine(x[0][0]), "test" in x[0][0].lower(), len(x[0][0]), x[0][0], x[0][1]))
            defs, hits = [h for h, d in hits if d], [h for h, _d in hits]
            if program and not defs:  # (another program's flag, passed on here: not this folder's)
                return Ref(surface, "cli", "not_found", reason=f"no code in this folder defines {program}'s {flag}")
            defs = [h for h in defs if not mine(h[0])] or defs  # (mytool's own, if it has one)
            role = "definition" if defs else "used"
            files = list(dict.fromkeys(p for p, _n, _t in defs or hits))
            locs = [{"role": role, "path": p, "line": n, "resolver": "grep", "note": _first_line(t.strip(), 80)}
                    for p, n, t in (next(h for h in defs or hits if h[0] == path) for path in files[:20])]
            if len(files) == 1:
                return Ref(surface, "cli", "resolved", f"{files[0]}::{flag}", locations=locs)
            return Ref(surface, "cli", "ambiguous", flag, locations=locs,
                       reason=f"{len(files)} files {'define' if defs else 'use'} {flag}")
        if not how:
            return Ref(surface, "cli", "not_found", reason="not a command Signal can place")
        if how[0] == "module":
            files, dirs, _by_name = self._files(st)
            path = how[1].replace(".", "/")
            hits = sorted((f for f in files for end in (path + ".py", path + "/__main__.py", path + "/__init__.py")
                           if f == end or f.endswith("/" + end)), key=lambda f: (f.endswith("__init__.py"), len(f), f))
            if not hits:
                return Ref(surface, "cli", "not_found", reason=f"no module {how[1]} in this folder")
            locs = [{"role": "module", "path": h, "line": None, "resolver": "files", "note": f"python -m {how[1]}"}
                    for h in hits[:20]]
            return Ref(surface, "cli", "resolved" if len(hits) == 1 else "ambiguous", hits[0], locations=locs,
                       reason="" if len(hits) == 1 else f"{len(hits)} modules named {how[1]}")
        if how[0] == "kube":  # (kubectl rollout restart deploy/x: what it acts on)
            ref = self._kube_ref(surface, how[1], st, meta)
            ref.surface, ref.kind = surface, "cli"
            return ref
        ref = self._file(how[1], st, meta)  # (the script it runs, or the file it's given)
        ref.surface, ref.kind = surface, "cli"
        for loc in ref.locations:
            loc["role"], loc["note"] = "script", f"runs {loc['path']}"
        return ref

    # ---- Kubernetes (see "signal: Kubernetes"): the repository's manifests, charts and overlays, and the kubeconfig
    def _kube(self, surface, st, meta):
        """A Kubernetes name (ref_kube); a word a reply calls a Deployment or a context ("the `builder` NodePool") is a
        name of that kind."""
        hint = ((meta or {}).get("hints") or {}).get(surface)
        return self._kube_ref(surface, ref_kube(surface) or ("name", ref_key(surface)), st, meta, hint)

    def _kube_ref(self, surface, how, st, meta, hint=None):
        """A Kubernetes name as ref_kube or ref_kube_command reads it (how): the objects the repository defines with that
        name (a pod's: its workload), the chart value or key path, the release, or the kubeconfig context. hint: the
        kind the reply gave it, or "context"."""
        what = how[0]
        if what == "values":
            return self._kube_values(surface, how[1], st)
        if what == "field":
            return self._kube_field(surface, how[1], st, meta) or (
                self._symbol(surface, st, meta) if not surface.startswith(".") else
                Ref(surface, "config", "not_found", reason=f"no manifest or values file has the key path {how[1]}"))
        if what == "release":
            return self._kube_release(surface, how[1], st)
        idx = self._kube_index(st)
        name, kind, hits = (how[2] if what in ("object", "selector") else how[1]), None, []
        if what == "selector":
            _w, key, value, ns = how
            hits = [(o, o["names"][0][0], f"labelled {key}={value}") for o in idx["objects"] if o["names"] and
                    o["labels"].get(key) == value and o["kind"] in KUBE_WORKLOADS + ("Service",) and
                    not (ns and o["namespace"] and o["namespace"] != ns)] or self._kube_find(value, None, ns, idx)
        elif what == "object":
            _w, kind, _n, ns = how
            hits = self._kube_find(name, kind, ns, idx) or \
                (self._kube_find(name, "Rollout", ns, idx) if kind == "Deployment" else [])  # (Argo's stand-in)
        elif what == "name" and hint != "context":
            kind = hint if hint and (hint in KUBE_ORDER or _KUBE_PASCAL.fullmatch(hint)) else None
            hits = (self._kube_find(name, kind, None, idx) if kind else []) or self._kube_find(name, None, None, idx)
        if not hits and kind == "Namespace":
            hits = self._kube_namespaces(name, idx)
        if hits:
            return self._kube_objects_ref(surface, hits, kind)
        if what in ("context", "name") and (found := [c for c in kube_contexts() if name in (c["name"], c.get("cluster"))
                                                      or (kube_made_as(c) or ("", ""))[1] == name]):
            found.sort(key=lambda c: name not in (c["name"], c.get("cluster")))  # (the exact name first)
            return self._kube_context_ref(surface, found)
        if what == "name" and hint != "context" and (live := kube_running([name])):  # (no manifest has it: it runs)
            r = live[0]
            note = f"{r['kind']} {name}, running in {r['context']} · {r['namespace']} ({r['state']})" + \
                (f", and {len(live) - 1} more" if len(live) > 1 else "")
            return Ref(surface, "k8s", "resolved", f"{r['kind']}/{name}", data={
                "kind": r["kind"], "name": name, "namespace": r["namespace"], "live": True, "note": note})
        where = os.path.basename(self.root) or self.root
        if what == "context":
            reason = f"no context or cluster {name} in your kubeconfig" + ("" if kubeconfig_paths() else " (there is none)")
        else:
            reason = f"no Kubernetes object {name} in {where}'s manifests, Helm charts or kustomize overlays" + \
                (", nor a kubeconfig context" if what == "name" else "")
        return Ref(surface, "k8s", "not_found", reason=reason)

    def _kube_objects_ref(self, surface, hits, prefer=None):
        """The objects found for a name, its workload first (or the kind the reply gave it): one place (a chart, a
        kustomize base, a folder) defining them is found; several are several matches."""
        seen, mine = set(), []
        for h in hits:
            if (h[0]["path"], h[0]["line"]) not in seen:
                seen.add((h[0]["path"], h[0]["line"]))
                mine.append(h)
        rank = {k: i for i, k in enumerate(KUBE_ORDER)}
        mine.sort(key=lambda h: (h[0]["kind"] != prefer, rank.get(h[0]["kind"], len(rank)), h[0]["path"], h[0]["line"]))
        mine = mine[:30]

        def note(o, n, how):
            ns = o["namespace"] if o["namespace"] and not _KUBE_ACTION.search(o["namespace"]) else None
            return ", ".join(x for x in (n if n != surface else "", how, f"namespace {ns}" if ns else "") if x)
        locs = [{"role": o["kind"], "path": o["path"], "line": o["line"], "resolver": o["resolver"], "note": note(o, n, how)}
                for o, n, how in mine]
        first, name, how = mine[0]
        groups = sorted({o["group"] or "." for o, _n, _h in mine})
        ns = first["namespace"] if first["namespace"] and not _KUBE_ACTION.search(first["namespace"]) else None
        data = {"kind": first["kind"], "name": name, "namespace": ns, "chart": first["chart"], "release": first["release"],
                "overlay": first.get("overlay"), "how": how,
                "note": f"{first['kind']} {name}" + (f" ({how})" if how else "")}
        if len(groups) == 1:
            return Ref(surface, "k8s", "resolved", f"{first['kind']}/{name}", data=data, locations=locs)
        return Ref(surface, "k8s", "ambiguous", f"{first['kind']}/{name}", data=data, locations=locs,
                   reason=f"{len(groups)} places define it: {', '.join(groups[:4])}")

    @staticmethod
    def _kube_context_ref(surface, contexts):
        """A kubeconfig context (or a cluster no context names): its cluster, server, namespace and user's name. It has
        no location: the file holds credentials, so it isn't opened; its card says where it is."""
        c = next((c for c in contexts if not c.get("cluster_only")), contexts[0])
        what = "cluster" if c.get("cluster_only") else "context"
        made = kube_made_as(c) or ("", "")
        bits = [f"cluster {c['cluster']}" if c.get("cluster") and c["cluster"] != c["name"] else "",
                f"the {made[0]} cluster {made[1]}" if made[1] and made[1] not in (c["name"], c.get("cluster")) else "",
                f"namespace {c['namespace']}" if c.get("namespace") else "", "the current one" if c.get("current") else ""]
        note = f"Kubeconfig {what} {c['name']}" + (": " + ", ".join(b for b in bits if b) if any(bits) else "")
        data = {"context": c["name"], "cluster": c.get("cluster"), "server": c.get("server"),
                "namespace": c.get("namespace"), "user": c.get("user"), "kubeconfig": c["kubeconfig"],
                "line": c.get("line"), "current": bool(c.get("current")), "cluster_only": bool(c.get("cluster_only")),
                "note": note}
        return Ref(surface, "k8s", "resolved", f"{what} {c['name']}", data=data)

    def _kube_find(self, name, kind, ns, idx, pods=True):
        """The objects named name (of that kind, in namespace ns, when given): [(object, the name matched, how)]. Exact
        names first (as written, rendered for a release, as an overlay renames it; a generated ConfigMap's with its
        hash); else templated names whose literal parts match, the most literal first; else, for a pod, its workload."""
        exact, loose = [], []
        for o in idx["objects"]:
            if kind and o["kind"] != kind:
                continue
            if ns and o["namespace"] and not _KUBE_ACTION.search(o["namespace"]) and o["namespace"] != ns:
                continue
            hit = next(((n, how) for n, how in o["names"] if n == name), None) or \
                next(((n, (how + ", " if how else "") + "with the hash kustomize adds") for n, how in o["names"]
                      if o.get("generated") and re.fullmatch(re.escape(n) + r"-[a-z0-9]{10}", name)), None)
            if hit:
                exact.append((o, name, hit[1]))
            elif o["pattern"] and o["literal"] >= 4 and re.fullmatch(o["pattern"], name):
                loose.append((o, name, f"its template names it {o['raw']}"))
        if exact or loose:
            return exact or sorted(loose, key=lambda h: -h[0]["literal"])
        if pods and kind in (None, "Pod"):
            for owner_kind, owner in kube_pod_owners(name):
                kinds = {"CronJob": ("CronJob",), "Deployment": ("Deployment", "Rollout"),
                         "StatefulSet": ("StatefulSet",)}.get(owner_kind, ("Job", "DaemonSet", "ReplicaSet"))
                got = [(o, n, (how + "; " if how else "") + f"{name} is one of its pods")
                       for o, n, how in self._kube_find(owner, None, ns, idx, pods=False) if o["kind"] in kinds]
                if got:
                    return got
        return []

    def _kube_namespaces(self, name, idx):
        """Where the repository puts things in namespace name, when no Namespace object defines it: a kustomization's
        namespace:, a Helm release's -n, then the manifests that say so (the first in each file)."""
        out, files = [], set()
        place = lambda path, line, how: ({"kind": "Namespace", "path": path, "line": line, "resolver": "kube",  # noqa: E731
                                          "namespace": None, "group": f"namespace {name}", "chart": None,
                                          "release": None}, name, how)
        for d, k in sorted(idx["kustomizations"].items()):
            if k["namespace"] == name:
                out.append(place(k["path"], k.get("ns_line"), f"kustomize sets it for {d or '.'}"))
        for r in idx["releases"]:
            if r["namespace"] == name:
                out.append(place(r["path"], r["line"], f"helm -n, for the release {r['release']}"))
        for o in idx["objects"]:
            if o["namespace"] == name and o.get("ns_line") and o["path"] not in files and o["names"]:
                files.add(o["path"])
                out.append(place(o["path"], o["ns_line"], f"the namespace of {o['kind']} {o['names'][0][0]}"))
        return out

    def _kube_values(self, surface, key, st, fallback=True):
        """A Helm chart's value (.Values.image.tag, helm --set image.tag=...): where its values files set it (values.yaml
        first), then the templates that read it. Not in any: any YAML key of that path (_config), or None with no
        fallback."""
        idx = self._kube_index(st)
        parts, locs, charts = key.split("."), [], set()
        for root, c in idx["charts"].items():
            for vf in c["values_files"]:
                lines = self._text(vf).splitlines()
                for i, s in enumerate(lines):
                    if re.match(rf"\s*{re.escape(parts[-1])}\s*:", s) and ref_yaml_path(lines, i) == parts:
                        locs.append({"role": "value", "path": vf, "line": i + 1, "resolver": "helm",
                                     "note": f"the {c['name']} chart's {os.path.basename(vf)}"})
                        charts.add(root)
                        break
        if not locs:
            if not fallback:
                return None
            return self._config(surface, key) or Ref(surface, "config", "not_found",
                                                     reason=f"no Helm chart's values files have {key}")
        used = [(p, n) for p, n, t in self._grep(["-F", "-e", f".Values.{key}"], ["*.yaml", "*.yml", "*.tpl"], limit=200)
                if re.search(rf"\.Values\.{re.escape(key)}(?![\w.-])", t)]
        locs += [{"role": "used", "path": p, "line": n, "resolver": "helm", "note": f".Values.{key}"} for p, n in used[:12]]
        if len(charts) == 1:
            return Ref(surface, "config", "resolved", f"{next(iter(charts)) or '.'}::.Values.{key}", locations=locs)
        return Ref(surface, "config", "ambiguous", f".Values.{key}", reason=f"{len(charts)} charts have {key}",
                   locations=locs)

    def _kube_field(self, surface, path, st, meta):
        """A key path in manifests (spec.strategy.type, metadata.labels): the manifest, template and values lines whose
        key path ends with the most of it (a values file has a template's settings under keys of its own), the files
        the session touched first. None if no line has two of its keys in order (or its only one)."""
        parts, idx = path.split("."), self._kube_index(st)
        values = {vf for c in idx["charts"].values() for vf in c["values_files"]}
        touched, found = self._touched(meta), []
        key = re.compile(rf"\s*-?\s*{re.escape(parts[-1])}\s*:")
        for f in sorted({o["path"] for o in idx["objects"]} | values):
            lines = self._text(f).splitlines()
            for i, s in enumerate(lines):
                if key.match(s):
                    have = ref_yaml_path(lines, i)
                    k = next((k for k in range(len(parts), 0, -1) if ref_in_order(parts[-k:], have)), 0)
                    if k >= min(2, len(parts)):
                        found.append((k, f, i + 1, ".".join(have)))
        if not found:
            return None
        best = max(x[0] for x in found)
        top = sorted((x for x in found if x[0] == best), key=lambda x: (x[1] not in touched, x[1] in values, x[1], x[2]))
        locs = [{"role": "key", "path": f, "line": n, "resolver": "yaml", "note": have} for _k, f, n, have in top[:30]]
        if len(locs) == 1:
            return Ref(surface, "config", "resolved", f"{locs[0]['path']}::{path}", locations=locs)
        return Ref(surface, "config", "ambiguous", path, reason=f"{len(top)} manifests have {path}", locations=locs)

    def _kube_release(self, surface, release, st):
        """A Helm release: the lines that install it (helm upgrade --install), then its chart."""
        rs = [r for r in self._kube_index(st)["releases"] if r["release"] == release]
        if not rs:
            return Ref(surface, "k8s", "not_found",
                       reason=f"nothing in {os.path.basename(self.root) or self.root} installs a Helm release {release}")
        charts = list(dict.fromkeys(r["chart"] for r in rs))
        locs = [{"role": "release", "path": r["path"], "line": r["line"], "resolver": "helm",
                 "note": f"installs {release} from {r['chart'] or '.'}" + (f" in {r['namespace']}" if r["namespace"] else "")}
                for r in rs] + [{"role": "chart", "path": os.path.join(c, "Chart.yaml"), "line": 1, "resolver": "helm",
                                 "note": ""} for c in charts]
        data = {"release": release, "chart": charts[0], "namespace": rs[0]["namespace"],
                "note": f"Helm release {release} of {charts[0] or '.'}"}
        return Ref(surface, "k8s", "resolved" if len(charts) == 1 else "ambiguous", f"release {release}", data=data,
                   locations=locs[:30], reason="" if len(charts) == 1 else f"installed from {len(charts)} charts")

    def _build_routes(self, st):
        """FastAPI handlers with their full paths (decorator + APIRouter prefix + include_router prefix),
        OpenAPI snapshots' paths, and nginx locations."""
        texts = {}

        def lines(path):
            if path not in texts:
                try:
                    with open(os.path.join(self.root, path), encoding="utf-8", errors="replace") as f:
                        texts[path] = f.read().splitlines()
                except OSError:
                    texts[path] = []
            return texts[path]

        def statement(path, n):  # the line and its continuation lines, while brackets are open
            out, depth = [], 0
            for s in lines(path)[n - 1:n + 11]:
                out.append(s.strip())
                depth += s.count("(") + s.count("[") - s.count(")") - s.count("]")
                if depth <= 0:
                    break
            return " ".join(out)

        def enclosing_def(path, n):
            ls = lines(path)
            indent = len(ls[n - 1]) - len(ls[n - 1].lstrip()) if n <= len(ls) else 0
            for j in range(n - 2, -1, -1):
                s = ls[j]
                ind = len(s) - len(s.lstrip())
                if s.strip() and ind < indent:
                    if d := re.match(r"\s*(?:async\s+)?def\s+(\w+)", s):
                        return d.group(1)
                    if ind == 0:
                        return None
                    indent = ind
            return None

        prefix_re = re.compile(r"prefix\s*=\s*[rf]?[\"']([^\"']*)[\"']")
        routers = {}
        for path, n, _t in self._grep(["-E", r"[A-Za-z_][A-Za-z0-9_]*[[:space:]]*=[[:space:]]*APIRouter\("], ["*.py"]):
            s = statement(path, n)
            if m := re.match(r"([A-Za-z_]\w*)\s*=\s*APIRouter\(", s):
                p = prefix_re.search(s)
                routers.setdefault(path, []).append({"line": n, "var": m.group(1), "prefix": p.group(1) if p else "",
                                                     "builder": enclosing_def(path, n)})
        def aliases(path):  # import aliases in a file: {alias: "module.name"}
            out = {}
            for m in re.finditer(r"from\s+([\w.]+)\s+import\s+\(?([^)\n]*(?:\n[^)\n]*)*?)\)?\s*$",
                                 "\n".join(lines(path)), re.M):
                for name, alias in re.findall(r"(\w+)\s+as\s+(\w+)", m.group(2)):
                    out[alias] = f"{m.group(1).split('.')[-1]}.{name}"
            return out
        includes = []
        for path, n, _t in self._grep(["-E", r"include_router\("], ["*.py"]):
            s = statement(path, n)
            if m := re.search(r"include_router\(\s*([\w.]+)", s):
                p = prefix_re.search(s)
                token = m.group(1)
                includes.append((aliases(path).get(token, token), p.group(1) if p else ""))
        handlers = []
        verbs = "get|post|put|patch|delete|head|options|api_route|websocket"
        for path, n, _t in self._grep(["-E", rf"@[A-Za-z_][A-Za-z0-9_]*\.({verbs})\("], ["*.py"]):
            s = statement(path, n)
            m = re.search(rf"@(\w+)\.({verbs})\(\s*(?:path\s*=\s*)?[rf]?[\"']([^\"']*)[\"']", s)
            if not m:
                continue
            var, verb, route = m.groups()
            if verb == "api_route":
                ms = re.search(r"methods\s*=\s*[\[(]([^\])]*)", s)
                methods = {x.upper() for x in re.findall(r"[\"'](\w+)[\"']", ms.group(1))} if ms else set()
            else:
                methods = {"WEBSOCKET" if verb == "websocket" else verb.upper()}
            mine = [r for r in routers.get(path, []) if r["var"] == var]
            router = ([r for r in mine if r["line"] < n] or mine or [None])[-1]
            own, builder = (router["prefix"], router["builder"]) if router else ("", None)
            stem = os.path.splitext(os.path.basename(path))[0]
            prefixes = set()
            for token, prefix in includes:
                last, mod = token.split(".")[-1], (token.split(".")[-2] if "." in token else None)
                if (builder and last == builder and mod in (None, stem)) or (not builder and (
                        token in (f"{stem}.{var}", f"{stem}_router", f"{stem}_{var}") or (var != "router" and token == var))):
                    prefixes.add(prefix)
            fn = next((d.group(1) for s2 in lines(path)[n:n + 15]
                       if (d := re.match(r"\s*(?:async\s+)?def\s+(\w+)", s2))), "")
            paths = {re.sub(r"/{2,}", "/", f"{inc}{own}{route}") or "/" for inc in (prefixes or {""})}
            handlers.append({"path": path, "line": n, "methods": methods, "paths": paths, "fn": fn})
        specs = []
        files = self._files(st)[0]
        for path in [f for f in files if "openapi" in os.path.basename(f).lower() and f.endswith(".json")][:100]:
            try:
                if os.path.getsize(os.path.join(self.root, path)) > 3_000_000:
                    continue
                doc = json.loads("\n".join(lines(path)))
            except (OSError, ValueError):
                continue
            paths = doc.get("paths") if isinstance(doc, dict) else None
            for route, ops in paths.items() if isinstance(paths, dict) else ():
                key = json.dumps(route) + ":"
                n = next((i for i, s in enumerate(lines(path), 1) if key in s), 1)
                methods = {k.upper() for k in (ops if isinstance(ops, dict) else {})
                           if k.upper() in _REF_METHODS.split("|")}
                specs.append({"path": path, "line": n, "route": route, "methods": methods})
        edges = []
        for path, n, text in self._grep(["-E", r"^[[:space:]]*location[[:space:]]+(=|\^~)?[[:space:]]*/"],
                                        ["*.conf", "*.conf.template", "*nginx*"]):
            if m := re.match(r"\s*location\s+(=|\^~)?\s*(/[^\s{]*)", text):
                edges.append({"path": path, "line": n, "prefix": m.group(2), "exact": m.group(1) == "="})
        return {"handlers": handlers, "specs": specs, "edges": edges}


# --------------------------------------------------------- tool schemas --
# Claude Code's tools as Anthropic describes them, for Signal's tool cards (a reply that talks about the Agent
# tool, `TodoWrite`, `Read`): input and output schemas from the Agent SDK package that matches the installed CLI
# (SDK 0.3.N ships sdk-tools.d.ts, generated from CLI 2.1.N's own JSON Schemas), descriptions and permissions
# from the tools reference in the docs; and what's local: the calls a session made, subagent and skill
# definitions, and the tools/list of MCP servers you configured. Fetched once per CLI version, with your OK.

TOOL_SDK_PACKAGE = "@anthropic-ai/claude-agent-sdk"
TOOL_DOCS_MD = "https://code.claude.com/docs/en/tools-reference.md"
TOOL_DOCS_PAGE = "https://code.claude.com/docs/en/tools-reference"
TOOL_NAMES = set()     # every tool name seen: the CLI's init lists, the catalogue, the calls in the chat
SUBAGENTS = {}         # subagent type -> {"description", "model", "tools", "path"} (definitions; built-ins too)
SKILLS = {}            # skill name -> {"description", "path"}
TOOL_SDK_RENAME = {"FileRead": "Read", "FileEdit": "Edit", "FileWrite": "Write", "Mcp": None,
                   "ListMcpResources": "ListMcpResourcesTool", "ReadMcpResource": "ReadMcpResourceTool"}
SUBAGENTS_BUILT_IN = {"general-purpose": "researches, searches and runs multi-step tasks (all tools)",
                      "Explore": "read-only search across a codebase", "Plan": "designs an implementation plan",
                      "claude-code-guide": "answers questions about Claude Code, the Agent SDK and the API",
                      "statusline-setup": "sets up the status line", "fork": "a copy of the current conversation"}


def tool_dir():
    return os.path.join(_data_home(), "baude", "tool-schemas")


# (MACOS_DESKTOP_CLAUDE, claude_exe and claude_version: baudelib.claude)


def tool_fetch(cli):
    """Fetch the Agent SDK package for this CLI (from npm's registry: only its sdk-tools.d.ts and manifest.json
    are kept) and the docs' tools reference, into tool_dir()/<cli>. Returns (ok, what happened)."""
    import io
    import tarfile
    import urllib.request

    def get(url, limit=20_000_000):
        req = urllib.request.Request(url, headers={"User-Agent": "baude"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.read(limit)
    major, minor, patch = (cli or "0.0.0").split(".")
    version = f"0.3.{patch}" if (major, minor) == ("2", "1") else None
    base = f"https://registry.npmjs.org/{TOOL_SDK_PACKAGE}"
    folder = os.path.join(tool_dir(), cli or "unknown")
    try:
        try:
            if version is None:
                raise OSError("no matching SDK version")
            tgz = get(f"{base}/-/claude-agent-sdk-{version}.tgz")
        except OSError:  # no SDK release for this CLI: the latest one's (its manifest says which CLI)
            version = json.loads(get(f"{base}/latest"))["version"]
            tgz = get(f"{base}/-/claude-agent-sdk-{version}.tgz")
        os.makedirs(folder, exist_ok=True)
        with tarfile.open(fileobj=io.BytesIO(tgz), mode="r:gz") as tar:
            for name in ("sdk-tools.d.ts", "manifest.json"):
                member = tar.extractfile(f"package/{name}")
                if member is not None:
                    with open(os.path.join(folder, name), "wb") as f:
                        f.write(member.read())
        with open(os.path.join(folder, "sdk-version"), "w") as f:
            f.write(version)
        with open(os.path.join(folder, "tools-reference.md"), "wb") as f:
            f.write(get(TOOL_DOCS_MD, 3_000_000))
    except (OSError, ValueError, KeyError, tarfile.TarError) as e:
        return False, f"couldn't fetch the tool schemas: {e}"
    return True, f"Agent SDK {version} and the tools reference, into {pretty_path(folder)}"


def _ts_fields(body):
    """The top-level fields of a generated TypeScript interface body: [{name, type, required, doc}]."""
    fields, doc, i, lines = [], "", 0, body.split("\n")
    while i < len(lines):
        line = lines[i]
        if re.match(r"^  /\*\*", line):  # a field's JSDoc
            chunk = [line]
            while "*/" not in lines[i] and i + 1 < len(lines):
                i += 1
                chunk.append(lines[i])
            doc = " ".join(re.sub(r"^\s*(/\*\*|\*/|\*)\s?", "", c).replace("*/", "").strip() for c in chunk).strip()
        elif m := re.match(r"^  (\"?[\w$-]+\"?)(\??): (.*)$", line):
            text, depth = m.group(3), m.group(3).count("{") - m.group(3).count("}")
            while (depth > 0 or not text.rstrip().endswith(";")) and i + 1 < len(lines):
                i += 1
                text += "\n" + lines[i]
                depth += lines[i].count("{") - lines[i].count("}")
            typ = re.sub(r"\s+", " ", re.sub(r"/\*\*.*?\*/", "", text, flags=re.S)).strip().rstrip(";").strip()
            fields.append({"name": m.group(1).strip('"'), "type": typ, "required": not m.group(2), "doc": doc})
            doc = ""
        elif line.strip() and not line.startswith("  "):
            doc = ""
        i += 1
    return fields


def tool_parse_sdk(text):
    """{tool name: {"input": [field], "output": type text}} from the SDK's sdk-tools.d.ts."""
    tools, starts = {}, [(m.start(), m) for m in re.finditer(r"^export (?:interface (\w+) \{|type (\w+) =)", text, re.M)]
    for k, (at, m) in enumerate(starts):
        name = m.group(1) or m.group(2)
        chunk = text[at:starts[k + 1][0] if k + 1 < len(starts) else len(text)]
        chunk = re.sub(r"\n/\*\*(?:(?!\*/).)*\*/\s*$", "", chunk, flags=re.S).rstrip()  # (the next one's JSDoc)
        for suffix, key in (("Input", "input"), ("Output", "output")):
            if not name.endswith(suffix) or name in ("ToolInputSchemas", "ToolOutputSchemas"):
                continue
            base = name[:-len(suffix)]
            tool = TOOL_SDK_RENAME.get(base, base)
            if not tool:
                continue
            entry = tools.setdefault(tool, {})
            if key == "input" and m.group(1):
                entry["input"] = _ts_fields(chunk[chunk.index("{") + 1:chunk.rindex("}")])
            entry[key + "_text"] = chunk.strip()
    return tools


def tool_parse_docs(text):
    """{tool name: {"description", "permission", "anchor"}} from the docs' tools reference table."""
    tools = {}
    for m in re.finditer(r"^\| `([\w-]+)` \| (.*?) \| (Yes|No)[^|]*\|\s*$", text, re.M):
        desc = m.group(2)
        anchor = next(iter(re.findall(r"\(#([\w-]+)\)", desc)), None)
        desc = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", desc)
        desc = re.sub(r"\s*See [^.|]*(behavior|tool)[^.|]*(\.|$)", "", desc).strip()
        tools[m.group(1)] = {"description": desc, "permission": m.group(3) == "Yes", "anchor": anchor}
    return tools


class ToolCatalog:
    """The tools of the installed Claude Code, merged from the cached SDK types and docs (tool_fetch), and from
    MCP servers' tools/list (mcp_tool_schemas)."""

    def __init__(self):
        self.tools, self.cli, self.sdk, self.loaded = {}, None, None, False

    def load(self):
        """Read the cache for this CLI's version (else the newest cached). False if nothing is cached."""
        cli = claude_version()
        root = tool_dir()
        folder = os.path.join(root, cli or "unknown")
        if not os.path.isdir(folder):
            older = sorted((d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d))),
                           key=lambda v: [int(x) if x.isdigit() else 0 for x in v.split(".")]) if os.path.isdir(root) else []
            if not older:
                return False
            folder = os.path.join(root, older[-1])
        try:
            sdk = tool_parse_sdk(open(os.path.join(folder, "sdk-tools.d.ts"), encoding="utf-8").read())
        except OSError:
            sdk = {}
        try:
            docs = tool_parse_docs(open(os.path.join(folder, "tools-reference.md"), encoding="utf-8").read())
        except OSError:
            docs = {}
        try:
            self.sdk = open(os.path.join(folder, "sdk-version")).read().strip()
        except OSError:
            self.sdk = None
        self.cli = os.path.basename(folder)
        for name in set(sdk) | set(docs):
            entry = {**docs.get(name, {}), **sdk.get(name, {}), "source": "anthropic"}
            self.tools[name] = {**self.tools.get(name, {}), **entry}
        TOOL_NAMES.update(self.tools)
        self.loaded = bool(self.tools)
        return self.loaded

    def add_mcp(self, server, tools):
        """A server's tools/list: mcp__<server>__<tool>, with its JSON Schema turned into fields."""
        norm = re.sub(r"[^a-zA-Z0-9_-]", "_", server)
        for t in tools:
            schema = t.get("inputSchema") or {}
            required = set(schema.get("required") or [])
            fields = [{"name": k, "type": _json_type(v), "required": k in required, "doc": (v or {}).get("description", "")}
                      for k, v in (schema.get("properties") or {}).items()]
            name = f"mcp__{norm}__{t.get('name')}"
            self.tools[name] = {"description": t.get("description", ""), "input": fields, "source": "mcp",
                                "server": server}
            TOOL_NAMES.add(name)

    def get(self, name):
        return self.tools.get(name)


def _json_type(schema):
    """A JSON Schema's type as a short TypeScript-ish string."""
    schema = schema or {}
    if "enum" in schema:
        return " | ".join(json.dumps(v) for v in schema["enum"][:12])
    t = schema.get("type")
    if t == "array":
        return f"{_json_type(schema.get('items'))}[]"
    if isinstance(t, list):
        return " | ".join(t)
    if t == "object" and schema.get("properties"):
        return "{ " + "; ".join(f"{k}: {_json_type(v)}" for k, v in list(schema["properties"].items())[:6]) + " }"
    return t or ("any" if not schema.get("anyOf") else " | ".join(_json_type(x) for x in schema["anyOf"][:6]))


TOOLS = ToolCatalog()


def _frontmatter(path):
    """A markdown file's YAML frontmatter as flat strings (name, description, model, tools...)."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read(20000)
    except OSError:
        return {}
    m = re.match(r"---\s*\n(.*?)\n---", text, re.S)
    out = {}
    for line in (m.group(1).split("\n") if m else []):
        if mm := re.match(r"([\w-]+):\s*(.*)", line):
            out[mm.group(1)] = mm.group(2).strip().strip("'\"")
    return out


def local_definitions(root):
    """Subagent types and skills defined for a folder (its .claude) and for you (~/.claude):
    ({type: info}, {skill: info}). Built-in subagent types are listed too, without a file."""
    agents = {name: {"description": d, "built_in": True} for name, d in SUBAGENTS_BUILT_IN.items()}
    skills = {}
    homes = [os.path.join(root, ".claude"), os.path.expanduser("~/.claude")]
    for home in homes:
        adir = os.path.join(home, "agents")
        for f in sorted(os.listdir(adir)) if os.path.isdir(adir) else []:
            if f.endswith(".md"):
                fm = _frontmatter(os.path.join(adir, f))
                name = fm.get("name") or f[:-3]
                agents.setdefault(name, {"description": fm.get("description", ""), "model": fm.get("model"),
                                         "tools": fm.get("tools"), "path": os.path.join(adir, f)})
        sdir = os.path.join(home, "skills")
        for dirpath, dirs, files in os.walk(sdir) if os.path.isdir(sdir) else []:
            if dirpath.count(os.sep) - sdir.count(os.sep) >= 3:  # (a skill is a folder or two down, not deeper)
                dirs[:] = []
            if "SKILL.md" in files:
                fm = _frontmatter(os.path.join(dirpath, "SKILL.md"))
                name = fm.get("name") or os.path.basename(dirpath)
                skills.setdefault(name, {"description": fm.get("description", ""),
                                         "path": os.path.join(dirpath, "SKILL.md")})
    return agents, skills


def mcp_servers(root):
    """MCP servers configured for a folder: its .mcp.json, and ~/.claude.json (yours, and this project's)."""
    servers = {}
    try:
        with open(os.path.join(root, ".mcp.json")) as f:
            servers.update(json.load(f).get("mcpServers") or {})
    except (OSError, ValueError):
        pass
    try:
        with open(os.path.expanduser("~/.claude.json")) as f:
            cfg = json.load(f)
        servers.update(cfg.get("mcpServers") or {})
        servers.update(((cfg.get("projects") or {}).get(root) or {}).get("mcpServers") or {})
    except (OSError, ValueError, AttributeError):
        pass
    return {name: s for name, s in servers.items() if isinstance(s, dict)}


def mcp_tool_schemas(server, root, timeout=20):
    """A stdio MCP server's tools/list: start it, initialize, list its tools, stop it. [] for anything else
    (an HTTP server, a failure)."""
    if server.get("type") not in (None, "stdio") or not server.get("command"):
        return []
    try:
        p = subprocess.Popen([server["command"], *(server.get("args") or [])], cwd=root, stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                             env={**os.environ, **{k: str(v) for k, v in (server.get("env") or {}).items()}})
    except OSError:
        return []
    answers, ready = {}, {1: threading.Event(), 2: threading.Event()}

    def read():
        for line in p.stdout:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if msg.get("id") in ready:
                answers[msg["id"]] = msg
                ready[msg["id"]].set()
        for ev in ready.values():  # (it exited)
            ev.set()

    def send(msg):
        p.stdin.write(json.dumps({"jsonrpc": "2.0", **msg}) + "\n")
        p.stdin.flush()
    threading.Thread(target=read, daemon=True).start()
    try:
        send({"id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                                                          "clientInfo": {"name": "baude-signal", "version": "1"}}})
        if ready[1].wait(timeout) and "result" in answers.get(1, {}):  # (it answers before it's told anything else)
            send({"method": "notifications/initialized"})
            send({"id": 2, "method": "tools/list", "params": {}})
            ready[2].wait(timeout)
    except (BrokenPipeError, OSError):
        pass
    finally:
        try:
            p.kill()
        except OSError:
            pass
    return ((answers.get(2) or {}).get("result") or {}).get("tools") or []


# ------------------------------------------------------------- signal: the reader --
# The rules above find what has a recognizable shape, at once. The reader does the rest: one model session per
# chat (Sonnet, or Haiku) that reads it from the start, the way a person following along would: your messages,
# what Claude ran and saw (its tool calls and the start of each result, and its subagents' work), and each
# reply. For every name in every reply it decides what the name points at there, with Signal's own lookups
# as its tools (what's known, a fresh search like the rules', the live database's schema) beside its own Read
# and Grep, and records each decision through a tool. Baude checks each one mechanically (the reply writes the
# name, the file exists, the name is near the line) and keeps it for that reply: the same word can be a field in
# one reply and English in the next. Separately, what Claude itself read or grepped before a reply is evidence
# no model is needed for (ref_evidence): a name found there in exactly one place is linked to it.

SIGNAL_READER_INTRO = """You are Baude's Signal reader. Baude shows a person an AI coding assistant's conversation \
(Claude Code) and turns the names in Claude's replies into links. You read the conversation in order, part by \
part, the way a person following along would, and decide for each reply which names in it a reader may want to \
jump to, and where each one points, in this repository ({root}).

Each part has, in order: the user's messages ([you]){what}, and Claude's replies ([reply N]). After it: the \
names Baude's rules found in those replies, with the rules' answers{seen}.

Decide every name in the part's replies a reader may want to open: files and folders, functions, classes, \
methods, fields, database tables and columns, constants, env vars, HTTP routes, config keys, Kubernetes and \
Helm objects, services and components, packages, commands, tests, commits, branches, PRs, external tools, and \
the tools Claude itself calls. Read every sentence: many of these are written in plain prose, without \
backticks: services and components ("the Scheduler", "the API gateway", "the Admin Console", "the nginx edge"), \
Kubernetes objects ("the NodePool", "the Argo Rollout"), products ("Karpenter"). Baude's rules only see \
backticked shapes, so the prose names are yours to add; a long reply usually names 20 to 60 things. A proper \
name of a part of this system ("the Admin Console", "pgsync", "the Dev Box") is a name: find its folder. Look everywhere: \
prose, backticks, and inside code blocks and diagrams. Spans of code and config count as well as names: a URL \
or host (`http://api-svc:8001`, `job-<hex>.example.com`: where it's built or configured), a command (`helm \
upgrade`, `--set imageTag=$SHA`: where it's run), a SQL fragment, an error message (`failed: runner stopped`: \
where it's raised), a header. When a backticked span is longer than a name (`strategy: Recreate`, \
`keep_dist=package == "x"`), decide the name inside it as well as the span. A word is a name only where the \
reply uses it as one: `step` in "the slider's `step`" is a field; "the next step" is English. Skip generic \
ideas, numbers and secrets.

Every name gets a decision, including the ones that aren't in this repository: outside tools, products, \
libraries, services and their commands (Postgres, Vite, FastAPI, Keycloak, Chrome, the Anthropic API, `npm ci`, \
`uv sync`, `docker exec`) are not_in_code, why external; a name the conversation proposes that isn't written \
yet is proposed; one in another checkout or branch, on another machine, or only in a running system (a job id, \
an org or app someone made, a container, a file in another repository) is elsewhere. Marking them tells the \
person they won't find it in this code.

Commands and flags: `python -m pkg.mod` opens the module; `node scripts/shot.mjs`, `bash scripts/x.sh`, \
`build.mjs --check` open the script they run; a flag (`--from`, `--org`) opens where this repository's command \
defines it (its add_argument or option), in the command the reply is about. Routes, however they're written, \
open their handler: in full (`GET /v1/orgs/{{org}}/jobs/{{job}}`), the end of one \
(`POST …/jobs/{{job}}/retry`), a prefix (`/v1/private/jobs/…`: the router), relative to a prefix the reply named \
just before (`GET /{{job}}`, `GET ?status=` under `/v1/orgs/{{org}}/jobs`), or in a diagram; \
lookup with kind route finds most, and a relative one is in the router of the prefix it follows.

Tools:
- mcp__signal__known: what Baude knows about names (the rules' places, earlier decisions{seen_tool}).
- mcp__signal__lookup: search the repository for a name the way Baude's rules do (definitions, files, env \
vars, routes, config keys, Kubernetes objects and Helm values, and language servers where they're on).
- mcp__signal__schema: the live database's tables and their columns (read-only).
- mcp__signal__kube: the Kubernetes objects this repository defines (raw manifests, Helm templates with the names \
their releases render them to, kustomize overlays) and the kubeconfig's contexts; by name or part of one (a pod's \
name finds its workload), kind or namespace, or an overview with nothing given. Kubeconfig files hold credentials: \
never read them yourself.
- Read, Grep, Glob and read-only Bash (git grep / log / show, rg, ls, find; one command per call), to read the \
code yourself.
- mcp__signal__decide: record your decisions for the part, all in one call; call it again to fix any it \
rejects. Each decision: reply (its number), name (exactly as the reply writes it, without backticks), verdict, \
and: for "link", path (repository-relative), line, role (definition, column, table, field, set, read, handler, \
folder, file...) and kind; for "not_in_code", why (proposed: the conversation suggests it but it isn't written; \
external: a third-party tool or product; elsewhere: another checkout, branch or machine; not_found); a short \
note on what it is, always.
  - link: the place a click should open: the definition; a table's model or migration; a column in its table's \
definition; a service's or package's folder; for an env var, the code that reads it (its settings class, \
os.environ), not where it's set; for a config key or a Kubernetes field, the template or values file the reply \
is talking about (the one it names nearby, or Claude just read), not the first file that has the word; for a \
Kubernetes object (a Deployment, a Service, a pod of one), the manifest or Helm template that defines it (kube \
gives its place). A kubeconfig context kube lists is Baude's to link: leave it.
  - plain: ordinary English there, not a name. A name written as code (a path, a dotted or snake_case name, a \
route, a flag, a command) is never plain: link it, or say why it isn't in the code.
  - not_in_code: after you've searched for it (lookup, or Grep); external needs no search.
decide answers with what it kept and rejected, and the names in the part that still have no decision: decide \
those too. A decision carries forward: a later reply that uses the name the same way gets it without a new decision, so \
decide a name again only where it means something else (known shows the decisions so far). A couple of \
searches per name is usually enough. Work read-only and never repeat secrets (keys, tokens, \
passwords). When the part's decisions are in, answer with one short line."""

SIGNAL_READER_PART = """Part {k}: replies {a} to {b}.

{events}

{names}
Decide every name in replies {a} to {b} with mcp__signal__decide."""

SIGNAL_READER_RESUME = """(Replies 1 to {n} were read in an earlier session: their decisions are known to \
mcp__signal__known. The conversation goes on from here.)"""

# the reader's session's environment: Signal's tools loaded at the start (not through a ToolSearch turn), no auto
# memory, no claude.ai connectors, no telemetry or update checks (SignalReader.session_args)
SIGNAL_READER_ENV = {"ENABLE_TOOL_SEARCH": "false", "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
                     "ENABLE_CLAUDEAI_MCP_SERVERS": "false", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"}

SIGNAL_READER_FRESH = """(Replies 1 to {n} were read before, in other sessions; you see only this part. What was \
decided for the names in it is listed under it, and mcp__signal__known has the rest. A decision carries forward: \
decide those names again only where they mean something else here.)"""

# the reader's tools (MCP), as Claude Code sees them; Baude runs them (Signal._job_tool)
SIGNAL_READER_TOOLS = [
    {"name": "known", "description": "What Baude knows about some names: the rules' answer (places, or why not "
     "found), the decisions made for them in earlier replies, and the places Claude itself read or grepped "
     "before the given reply that mention them.",
     "inputSchema": {"type": "object", "required": ["names"], "properties": {
         "names": {"type": "array", "items": {"type": "string"}, "maxItems": 60},
         "reply": {"type": "integer", "description": "the reply they're in (for what Claude had seen by then)"}}}},
    {"name": "lookup", "description": "Search the repository for a name as Baude's rules do: files by the end of "
     "their path, definitions (def, class, a field, a table or column), env vars (where set and read), HTTP "
     "routes, config keys, Kubernetes objects and Helm values; with language servers where they're on. Returns the "
     "matches.",
     "inputSchema": {"type": "object", "required": ["name"], "properties": {
         "name": {"type": "string"},
         "kind": {"type": "string", "description": "file, symbol, env, constant, route, config, k8s, commit, "
                                                   "branch"}}}},
    {"name": "schema", "description": "The live databases (Postgres and ArangoDB, in Docker or a local cluster; "
     "read-only): the tables (an ArangoDB database's collections) named "
     "table, with their columns; or the tables that have a column named column.",
     "inputSchema": {"type": "object", "properties": {"table": {"type": "string"}, "column": {"type": "string"}}}},
    {"name": "kube", "description": "The Kubernetes objects this repository defines (raw manifests; Helm chart "
     "templates, with the names the releases its helm upgrade --install lines name render them to; kustomize "
     "overlays, with their name prefixes and generated ConfigMaps), each with its place, and the kubeconfig's "
     "contexts (names, clusters and namespaces only). By name or part of one (a pod's name finds its workload), "
     "kind (Deployment, svc, ...) or namespace; with none of them, an overview of the charts, releases and kinds.",
     "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}, "kind": {"type": "string"},
                                                      "namespace": {"type": "string"}}}},
    {"name": "decide", "description": "Record decisions about names in replies. Each is checked: the reply must "
     "write the name, a linked path must exist (the line is moved to the nearest one with the name), and "
     "not_in_code needs a search first. Returns what was kept and what was rejected, and why.",
     "inputSchema": {"type": "object", "required": ["decisions"], "properties": {"decisions": {
         "type": "array", "maxItems": 200, "items": {"type": "object", "required": ["reply", "name", "verdict"],
                                                     "properties": {
             "reply": {"type": "integer"}, "name": {"type": "string"},
             "verdict": {"type": "string", "enum": ["link", "plain", "not_in_code"]},
             "kind": {"type": "string"}, "path": {"type": "string"}, "line": {"type": "integer"},
             "role": {"type": "string"}, "why": {"type": "string", "enum": ["proposed", "external", "elsewhere",
                                                                             "not_found"]},
             "note": {"type": "string"}}}}}}},
]

# the relay Claude Code starts as the reader's MCP server: stdio JSON-RPC in, Baude's socket out (no GTK)
SIGNAL_READER_MCP = r'''import json, socket, sys

sock = sys.argv[1]


def call(tool, args):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(sock)
    s.sendall((json.dumps({"tool": tool, "args": args}) + "\n").encode())
    data = b""
    while not data.endswith(b"\n"):
        chunk = s.recv(1 << 16)
        if not chunk:
            break
        data += chunk
    s.close()
    return json.loads(data or b"{}")


def send(msg):
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


for line in sys.stdin:
    try:
        msg = json.loads(line)
    except ValueError:
        continue
    method, mid = msg.get("method"), msg.get("id")
    if mid is None:
        continue  # (notifications)
    try:
        if method == "initialize":
            result = {"protocolVersion": (msg.get("params") or {}).get("protocolVersion") or "2025-06-18",
                      "capabilities": {"tools": {}}, "serverInfo": {"name": "signal", "version": "1"}}
        elif method == "tools/list":
            result = {"tools": call("_tools", {}).get("tools", [])}
        elif method == "tools/call":
            p = msg.get("params") or {}
            res = call(p.get("name"), p.get("arguments") or {})
            result = {"content": [{"type": "text", "text": json.dumps(res, ensure_ascii=False)}],
                      "isError": bool(res.get("error"))}
        else:
            send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "unknown method"}})
            continue
        send({"jsonrpc": "2.0", "id": mid, "result": result})
    except Exception as e:  # noqa: BLE001 (Baude went away: say so)
        send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32000, "message": str(e)}})
'''


def signal_reader_mcp_path():
    """The relay script, written into Baude's cache (where Claude Code can run it)."""
    path = os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "baude", "signal_mcp.py")
    try:
        with open(path, encoding="utf-8") as f:
            if f.read() == SIGNAL_READER_MCP:
                return path
    except OSError:
        pass
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(SIGNAL_READER_MCP)
    return path


def _ref_trim(text, limit):
    """text cut to about limit characters, its start and end kept."""
    text = text or ""
    if len(text) <= limit:
        return text
    return text[:int(limit * 0.7)].rstrip() + f" […{len(text) - limit} more…] " + text[-int(limit * 0.3):].lstrip()


def _ref_result_text(content):
    """A tool result's text (a string, or text blocks)."""
    if isinstance(content, str):
        return content
    return "\n".join(c.get("text", "") for c in content or [] if isinstance(c, dict) and c.get("type") == "text")


def _ref_call_line(name, inp):
    """A tool call in one line: its name and what it's about."""
    inp = inp if isinstance(inp, dict) else {}
    if name in ("Read", "Edit", "Write", "NotebookEdit", "MultiEdit"):
        what = inp.get("file_path") or inp.get("notebook_path") or ""
        if inp.get("offset") or inp.get("limit"):
            what += f" (from line {inp.get('offset') or 1}" + (f", {inp['limit']} lines)" if inp.get("limit") else ")")
    elif name == "Grep":
        what = f"{inp.get('pattern', '')!r}" + (f" in {inp['path']}" if inp.get("path") else "") + \
            (f" ({inp['glob']})" if inp.get("glob") else "")
    elif name == "Glob":
        what = f"{inp.get('pattern', '')}" + (f" in {inp['path']}" if inp.get("path") else "")
    elif name == "Bash":
        what = inp.get("command", "")
    elif name in ("Agent", "Task"):
        what = f"{inp.get('description', '')} ({inp.get('subagent_type') or 'general-purpose'})"
    else:
        what = json.dumps(inp, ensure_ascii=False)
    return f"{name}: {_first_line(str(what), 220)}"


def signal_reader_events(path):
    """A transcript, in order, as what a reader of it sees: [("you", text), ("call", id, name, input),
    ("result", id, text), ("reply", text), ("sub", tool_use_id, [events])]; a subagent's own events go under the
    call that started it (its transcript beside the session's names that call in its meta.json). Read at once: a
    chat that's followed as it grows is read on from where it was (SignalChat), to the same events."""
    if not os.path.isfile(path):
        return []
    import glob
    subs = {}
    sub_dir = os.path.join(os.path.splitext(path)[0], "subagents")
    for meta in glob.glob(os.path.join(sub_dir, "*.meta.json")):
        try:
            with open(meta, encoding="utf-8") as f:
                tid = json.load(f).get("toolUseId")
            with open(meta[:-len(".meta.json")] + ".jsonl", encoding="utf-8", errors="replace") as f:
                sub_items = history_items(f, sidechain=True)
        except (OSError, ValueError):
            continue
        if tid:
            subs[tid] = _ref_events_of(sub_items, {})
    try:  # (a line at a time: the events are a sliver of a transcript, which gets to hundreds of MB of tool output)
        with open(path, encoding="utf-8", errors="replace") as f:
            return [ev for line in f for ev in _ref_events_of(history_items((line,)), subs)]
    except OSError:
        return []


def _ref_events_of(items, subs):
    out = []
    for kind, content, *_rest in items:
        if kind == "user":
            text = content if isinstance(content, str) else "\n".join(
                c.get("text", "") for c in content or [] if isinstance(c, dict) and c.get("type") == "text")
            if text.strip():
                out.append(("you", text.strip()))
        elif kind == "assistant":
            for c in content if isinstance(content, list) else []:
                if not isinstance(c, dict):
                    continue
                if c.get("type") == "text" and c.get("text", "").strip():
                    out.append(("reply", c["text"]))
                elif c.get("type") == "tool_use":
                    out.append(("call", c.get("id"), c.get("name"), c.get("input") or {}))
                    if c.get("id") in subs:
                        out.append(("sub", c.get("id"), subs[c["id"]]))
        elif kind == "tool_results":
            for c in content if isinstance(content, list) else []:
                if isinstance(c, dict) and c.get("type") == "tool_result":
                    out.append(("result", c.get("tool_use_id"), _ref_result_text(c.get("content"))))
    return out


def signal_reader_render(events, full, sub=False):
    """Events as the text of a part: in full, Claude's tool calls with the start of each result and its subagents'
    work; else only your messages and the replies."""
    lines, calls = [], {}
    for ev in events:
        if ev[0] == "you" and not sub:
            lines.append(f"[you] {_ref_trim(ev[1], 2500)}")
        elif ev[0] == "you" and sub and full:
            lines.append(f"    [asked] {_ref_trim(ev[1], 400)}")
        elif ev[0] == "reply" and not sub:
            lines.append(f"[reply {ev[2]}]\n{ev[1]}")
        elif ev[0] == "reply" and sub and full:
            lines.append(f"    [subagent said] {_ref_trim(ev[1], 500)}")
        elif ev[0] == "call" and full:
            calls[ev[1]] = ev[2]
            lines.append(("    " if sub else "") + f"[Claude → {_ref_call_line(ev[2], ev[3])}]")
        elif ev[0] == "result" and full:
            name, pad = calls.get(ev[1]), "      " if sub else "  "
            limit = (300 if sub else 900) if name not in ("Agent", "Task") else 1200
            body = "\n".join(_ref_trim(ev[2] or "", limit).splitlines()[:12 if sub else 24])
            if body.strip():
                lines.append("\n".join(pad + "| " + l for l in body.splitlines()))
        elif ev[0] == "sub" and full:
            inner = signal_reader_render(ev[2], full, sub=True)
            if inner:
                lines.append(_ref_trim(inner, 6000))
    return "\n".join(lines)


_REF_READ_LINE = re.compile(r"^\s*(\d+)→(.*)$")  # Claude Code's Read: "   186→    text"
_REF_HIT_LINE = re.compile(r"^([\w./@+-]+?\.[\w]+)[:-](\d+)[:-](.*)$")  # grep / rg / git grep -n: path:line:text


_REF_SEEN_TOOLS = ("Read", "Edit", "Write", "MultiEdit", "Grep", "Bash")  # (the calls whose results show places)


def ref_evidence(events, root, checkouts, carry=None):
    """What Claude saw in its tools' results, in order: [(replies before it, path, line, text)]: the lines of the
    files it read, and grep's path:line hits. Paths are made relative to root (from root itself, or another
    checkout of the same repository: checkouts). carry ({"calls", "replies"}) is where the events before these left
    off, and goes on from here (SignalChat reads a chat on as it grows)."""
    out = []
    carry = {"calls": {}, "replies": 0} if carry is None else carry
    calls = carry["calls"]

    def rel(p):
        if not p:
            return None
        p = os.path.normpath(p)
        if not os.path.isabs(p):
            return p
        for top in (root, *checkouts):
            if top and p.startswith(top.rstrip("/") + os.sep):
                return os.path.relpath(p, top)
        return None
    for ev in events:
        if ev[0] == "reply":
            carry["replies"] += 1
        elif ev[0] == "call":
            if ev[2] in _REF_SEEN_TOOLS:  # (only these are kept: the carry lasts as long as the chat is followed)
                calls[ev[1]] = (ev[2], ev[3] if isinstance(ev[3], dict) else {})
            else:
                calls.pop(ev[1], None)
        elif ev[0] == "result" and ev[1] in calls:
            name, inp = calls[ev[1]]
            text, replies = ev[2] or "", carry["replies"]
            if name == "Read" and (path := rel(inp.get("file_path"))):
                out.append((replies, path, None, ""))
                for line in text.splitlines()[:4000]:
                    if m := _REF_READ_LINE.match(line):
                        out.append((replies, path, int(m.group(1)), m.group(2)[:300]))
            elif name in ("Edit", "Write", "MultiEdit") and (path := rel(inp.get("file_path"))):
                out.append((replies, path, None, ""))
            elif name in ("Grep", "Bash"):
                base = inp.get("path") if name == "Grep" else None
                for line in text.splitlines()[:2000]:
                    if m := _REF_HIT_LINE.match(line.strip()):
                        p = m.group(1)
                        if base and not os.path.isabs(p) and not p.startswith(base.rstrip("/")):
                            p = os.path.join(base, p) if os.path.isabs(base) else p
                        if path := rel(p):
                            out.append((replies, path, int(m.group(2)), m.group(3)[:300]))
        elif ev[0] == "sub":
            for r, p, n, t in ref_evidence(ev[2], root, checkouts):
                out.append((carry["replies"], p, n, t))
    return out


class SignalChat:
    """A chat as Signal reads it (main loop): its events (signal_reader_events, the replies numbered), its replies'
    texts and hashes, and what Claude saw (ref_evidence), read on from where it was as the transcript grows.
    Transcripts are only appended to, so a read parses only the whole lines added since (a line still being written
    waits till it's done), and each subagent's transcript likewise, its events going on under the call that started
    it. One rewritten or cut short (the last line read isn't there any more) is read again from the start. (Read
    whole whenever it grew, a 15 MB chat took 60 ms of the main loop per line another program appended, and the
    largest, 120 to 180 MB, close to a second.) The lists are replaced, not changed: the reader and the worker keep
    the ones they were given."""

    SUB_CALLS = ("Agent", "Task")  # the calls that start subagents: a place is kept after each for its evidence

    def __init__(self, path, root, checkouts):
        self.path, self.root, self.checkouts = path, root, checkouts
        self.sub_dir = os.path.join(os.path.splitext(path)[0], "subagents")
        self.stamp = None  # (the files' sizes and times when last read: while they're the same, nothing's new)
        self.behind = False  # (a read stopped for time: the rest next time, whatever the stamp)
        self._files = {}
        self.restart()

    def restart(self):
        self.events, self.texts, self.replies, self.evidence = [], [], [], []
        self.offset, self.last = 0, None  # the whole lines read, and the last one's (start, _line_sum)
        self.carry = {"calls": {}, "replies": 0}  # (ref_evidence's, from one read to the next)
        # each subagent, by its meta.json: {"stamp", "tid", "offset", "last", "events" (None: no transcript yet),
        # "carry", "seen" (its transcript's size and time)}; and each call that started one, in order (slots, and by
        # tool_use_id): where its evidence goes,
        # {"tid", "replies" (before it), "at", "n" (evidence[at:at + n]), "placed" (its events are in), "i"}
        self.subs, self.slots, self.slot_of = {}, [], {}

    def read(self, until=None):
        """Read what's new: all of it, or what there's time for till until (a time.monotonic()), the rest next time
        (behind; a line or a subagent's transcript at least, then the time's looked at). Returns the number of the
        first reply whose evidence may have changed (a new reply, or one after new evidence: 1 when read from the
        start, one past the last when only other events changed), or None if nothing did."""
        stamp = self._stamp()
        if stamp is None:
            self.behind = False  # (gone: what was read is all there is, till it's back)
            return None
        if stamp == self.stamp and not self.behind:
            return None
        self.stamp, self.behind = stamp, False
        self._owned, self._first, self._changed = False, None, False
        if self._read(until) is False:  # (rewritten or cut short: from the start)
            self.restart()
            self._owned, self._first, self._changed = False, 1, True
            if self._read(until) is False:
                self.stamp = None  # (still changing under it: again next time)
        return (self._first or len(self.replies) + 1) if self._changed else None

    def _stamp(self):
        try:
            st = os.stat(self.path)
        except OSError:
            return None
        self._files = {}  # (the subagents' files: name -> (size, time))
        try:
            with os.scandir(self.sub_dir) as found:
                for e in found:
                    if e.name.endswith((".meta.json", ".jsonl")) and not e.name.startswith("."):
                        s = e.stat()
                        self._files[e.name] = (s.st_size, s.st_mtime_ns)
        except OSError:
            pass
        return st.st_size, st.st_mtime_ns, sorted(self._files.items())

    def _own(self):
        """About to change the events or the evidence: copies of them, once a read."""
        if not self._owned:
            self.events, self.evidence, self._owned = list(self.events), list(self.evidence), True
        self._changed = True

    def _affects(self, reply):
        self._first = reply if self._first is None else min(self._first, reply)

    def _read(self, until=None):
        """On from where it was: False if a file isn't the one read. until: as read's."""
        # the subagents first (a call read below takes its subagent's events), each read only if its files changed
        metas = sorted(n for n in self._files if n.endswith(".meta.json"))
        if set(self.subs) - set(metas):
            return False  # (one is gone)
        for name in metas:
            sub = self.subs.setdefault(name, {"stamp": None, "tid": None, "offset": 0, "last": None, "events": None,
                                              "carry": {"calls": {}, "replies": 0}, "seen": None})
            meta = os.path.join(self.sub_dir, name)
            try:
                if self._files[name] != sub["stamp"]:  # (it's written again when the subagent finishes)
                    with open(meta, encoding="utf-8") as f:
                        tid = json.load(f).get("toolUseId")
                    if sub["tid"] and tid != sub["tid"]:
                        return False
                    sub["stamp"], sub["tid"] = self._files[name], tid
            except (OSError, ValueError, AttributeError):
                continue  # (still being written: next time)
            jsonl = name[:-len(".meta.json")] + ".jsonl"
            if not sub["tid"] or sub["events"] is not None and self._files.get(jsonl) == sub["seen"]:
                continue  # (its transcript is as it was)
            new = []
            got = self._lines(os.path.join(self.sub_dir, jsonl), sub["offset"], sub["last"],
                              lambda line: new.extend(_ref_events_of(history_items((line,), sidechain=True), {})))
            if got is None or got is False and sub["events"] is not None:
                return False  # (rewritten, or gone)
            if got is False:
                continue  # (no transcript yet)
            sub["offset"], sub["last"], sub["seen"] = *got, self._files.get(jsonl)
            if sub["events"] is None or new:
                sub["events"] = (sub["events"] or []) + new
                if self._sub_grew(sub, new) is False:
                    return False
            if until is not None and time.monotonic() > until:
                self.behind = True  # (the other subagents next time, then the chat: as if they were all read first)
                return None
        present = {s["tid"]: s["events"] for s in self.subs.values() if s["tid"] and s["events"] is not None}
        new = []
        got = self._lines(self.path, self.offset, self.last,
                          lambda line: new.extend(_ref_events_of(history_items((line,)), present)), until)
        if got is None:
            return False
        if got is False:
            return None
        self.offset, self.last = got
        self.behind = until is not None and time.monotonic() > until  # (stopped for time, or just done: next time)
        if not new:
            return None
        self._own()
        texts, hashes = [], []
        for i, ev in enumerate(new):  # (numbered, for the parts)
            if ev[0] == "reply":
                new[i] = ("reply", ev[1], len(self.texts) + len(texts) + 1)
                texts.append(ev[1])
                hashes.append(hashlib.sha1(ev[1].encode("utf-8", "replace")).hexdigest()[:16])
        if texts:
            self._affects(len(self.texts) + 1)
            self.texts, self.replies = self.texts + texts, self.replies + hashes
        self._evidence_of(new)
        self.events += new
        return None

    def _evidence_of(self, events):
        """New top-level events' evidence, after the rest; each call that starts a subagent keeps a place after it for
        the subagent's, which grows with its transcript."""
        run, after = [], None  # (after: the place kept by the call just before)
        by_tid = {s["tid"]: s for s in self.subs.values() if s["tid"]}

        def flush():
            if run:
                rows = ref_evidence(run, self.root, self.checkouts, self.carry)
                if rows:
                    self.evidence += rows
                    self._affects(rows[0][0] + 1)
                run.clear()
        for ev in events:
            if ev[0] == "sub":
                flush()
                slot = after if after is not None and after["tid"] == ev[1] else self._slot(ev[1])
                slot["placed"] = True
                rows = [(slot["replies"], p, n, t)
                        for _r, p, n, t in ref_evidence(ev[2], self.root, self.checkouts, by_tid[ev[1]]["carry"])]
                if rows:  # (at the end: the place was kept just now)
                    self.evidence += rows
                    slot["n"] += len(rows)
                    self._affects(slot["replies"] + 1)
                after = None
                continue
            run.append(ev)
            after = None
            if ev[0] == "call" and ev[2] in self.SUB_CALLS:
                flush()
                after = self._slot(ev[1])
        flush()

    def _slot(self, tid):
        slot = {"tid": tid, "replies": self.carry["replies"], "at": len(self.evidence), "n": 0, "placed": False,
                "i": len(self.slots)}
        self.slots.append(slot)
        self.slot_of.setdefault(tid, []).append(slot)
        return slot

    def _sub_grew(self, sub, new):
        """A subagent's transcript grew by new (or has just appeared): its events go on under each call that started
        it (put in after the call where it was read before the subagent appeared), and so does its evidence, in the
        place kept for it. False if the events aren't as read."""
        tid, slots = sub["tid"], self.slot_of.get(sub["tid"]) or ()
        if not slots:
            if any(ev[0] == "call" and ev[1] == tid for ev in self.events):
                return False  # (started by a call that isn't an Agent call, read before it appeared: from the start)
            return None  # (its call isn't read yet: its events go in with it)
        self._own()
        events, fresh = sub["events"], [s for s in slots if not s["placed"]]
        if len(fresh) < len(slots):
            for i, ev in enumerate(self.events):
                if ev[0] == "sub" and ev[1] == tid:
                    self.events[i] = ("sub", tid, events)
        if fresh:
            calls = [i for i, ev in enumerate(self.events) if ev[0] == "call" and ev[1] == tid and not (
                i + 1 < len(self.events) and self.events[i + 1][0] == "sub" and self.events[i + 1][1] == tid)]
            if len(calls) != len(fresh):
                return False
            for i in reversed(calls):
                self.events.insert(i + 1, ("sub", tid, events))
        grown = ref_evidence(new, self.root, self.checkouts, sub["carry"]) if len(fresh) < len(slots) else []
        whole = ref_evidence(events, self.root, self.checkouts, sub["carry"]) if fresh else []
        for slot in slots:
            rows = [(slot["replies"], p, n, t) for _r, p, n, t in (grown if slot["placed"] else whole)]
            slot["placed"] = True
            if rows:
                at = slot["at"] + slot["n"]
                self.evidence[at:at] = rows
                slot["n"] += len(rows)
                for later in self.slots[slot["i"] + 1:]:
                    later["at"] += len(rows)
                self._affects(slot["replies"] + 1)
        return None

    @staticmethod
    def _lines(path, offset, last, take, until=None):
        """Hands take each whole line of a transcript past offset, decoded, a line at a time (they're mostly tool
        output, up to hundreds of MB), and returns (offset, last) on from there. None if it isn't the file read (it's
        shorter, or its last line read, last: (start, _line_sum), isn't there any more), False if there's no file.
        until (a time.monotonic()): no more lines once it's passed, the rest left for next time."""
        try:
            f = open(path, "rb")
        except FileNotFoundError:
            return False
        except OSError:
            return offset, last
        with f:
            try:
                if os.fstat(f.fileno()).st_size < offset:
                    return None
                if last is not None:
                    f.seek(last[0])
                    if _line_sum(f.readline()) != last[1]:
                        return None
                f.seek(offset)
                for raw in f:
                    if not raw.endswith(b"\n") and not SignalChat._whole(raw):
                        break  # (still being written: read once it's done)
                    last, offset = (offset, _line_sum(raw)), offset + len(raw)
                    take(raw.decode("utf-8", "replace"))
                    if until is not None and time.monotonic() > until:
                        break
            except OSError:
                pass
        return offset, last

    @staticmethod
    def _whole(raw):
        """Whether a last line with no newline yet is a whole record (read at once, a file's last line counts)."""
        if not raw.rstrip().endswith(b"}"):
            return False
        try:
            json.loads(raw)
        except ValueError:
            return False
        return True


_REF_FILE_EXT = re.compile(r"\.(py|pyi|ts|tsx|js|jsx|mjs|cjs|css|scss|json|ya?ml|toml|md|sh|go|rs|java|rb|sql|html|"
                            r"txt|lock|cfg|ini|tpl|conf|template|tsp|mmd|env|example|tf|hcl|dockerfile)$", re.I)


def ref_seen(evidence, name, before):
    """The places Claude had seen name before reply number before (1-based): [(path, line)], latest first.
    A file name matches the files it read or edited; a code name, the lines that have it as a word."""
    word = re.sub(r":\d+(:\d+)?$", "", ref_key(name).strip("`")).rstrip("/")
    filish = "/" in word or bool(_REF_FILE_EXT.search(word))  # (a file: matched by path, never by its words)
    last = word if filish else re.split(r"[.:]", re.sub(r"\(.*\)$", "", word))[-1]
    if len(last) < 3:
        return []
    pat = re.compile(r"(?<![\w$])" + re.escape(last) + r"(?![\w$])")
    found = []
    for r, path, line, text in reversed(evidence):
        if r >= before:
            continue
        if filish:
            if path == word or path.endswith("/" + word) or (line is None and os.path.basename(path) == word):
                found.append((path, None))
        elif line is not None and pat.search(text):
            found.append((path, line))
        if len(found) >= 8:
            break
    seen, out = set(), []
    for p, n in found:
        if (p, n) not in seen:
            seen.add((p, n))
            out.append((p, n))
    return out


class SignalReader:
    """Signal's reader (see above): one session per chat, reading it in order; its decisions come back
    through Signal's tools (_job_tool). Main loop, apart from what it hands the worker.
    With "signal_history" "glossary", each part is read by a session of its own: what it knows of the replies
    before is the glossary (the decisions for the part's names come with it, and known has the rest), so its
    context doesn't grow along the chat."""

    PART_CHARS = {"full": 18000, "light": 9000}

    def __init__(self, ref):
        settings = load_settings()
        self.ref = ref
        self.model = settings.get("signal_model") or "sonnet"
        self.effort = settings.get("signal_effort") or "medium"
        self.full = (settings.get("signal_context") or "full") != "light"
        self.glossary_only = settings.get("signal_history") == "glossary"  # (a session per part)
        self.part_chars = int(settings.get("signal_part_chars") or 0) or None  # (characters a part, about)
        self.budget = float(settings.get("signal_budget", 5.0))  # dollars per glossary, per Baude run
        self.proc, self.path, self.events, self.offset = None, None, [], 0
        self.texts, self.replies = [], []  # the chat's replies (number - 1 -> text, hash)
        self.pos = 0  # the next event to read
        self.part = None  # the part being read: {"k", "a", "b", "started", "end"}
        self.parts, self.cost = 0, 0.0
        self.spent, self.soon, self.intro, self.over_budget_said = {}, None, True, False
        self.spare = None  # (glossary-only history: the next part's session, started while this part is read)
        self.evidence, self.stopped, self.searches = [], False, []  # (searches: its own Grep / Bash patterns)

    # ---- what to read
    def follow(self, path):
        """The chat to read (its transcript): from the start, or on from where it was."""
        if not path:
            return
        if path != self.path:
            self.restart_chat(path)
        ref = self.ref
        if len(ref.chat_events) <= len(self.events) and self.events:
            return
        self.events, self.texts, self.replies, self.evidence = \
            ref.chat_events, ref.chat_texts, ref.chat_replies, ref.chat_evidence
        self.schedule()

    def restart_chat(self, path):
        if self.proc is not None:
            self.proc.kill()
        self.drop_spare()
        self.proc, self.path, self.events, self.pos, self.part, self.intro = None, path, [], 0, None, True

    def schedule(self, ms=600):
        if self.soon is None and not self.stopped:
            self.soon = GLib.timeout_add(ms, self.pump)

    # ---- the steps (main loop)
    def pump(self):
        """Read the next part, once the rules have looked at its replies."""
        self.soon = None
        if self.part is not None or self.stopped or self.pos >= len(self.events):
            return False
        if self.cost >= self.budget:
            self.drop_spare()
            if not self.over_budget_said:
                self.over_budget_said = True
                self.ref.note(f"{self.model.capitalize()} stopped reading: it has spent {ref_money(self.cost)}, the "
                              f"cap (\"signal_budget\" in settings)")
            return False
        if self.ref.waiting:
            self.schedule(800)
            return False
        if self.pos == 0 and self.intro:  # (decided before: go on from the first reply without decisions)
            done = self.ref.decided_upto
            first = next((i for i, h in enumerate(self.replies) if h not in done), len(self.replies))
            if first >= len(self.replies):
                self.pos = len(self.events)
                return False
            if first > 0:
                self.pos = next(i for i, ev in enumerate(self.events) if ev[0] == "reply" and ev[2] == first + 1)
                while self.pos > 0 and self.events[self.pos - 1][0] != "reply":
                    self.pos -= 1  # (with what led to it)
                self.resume_from = first
        limit = self.part_chars or self.PART_CHARS["full" if self.full else "light"]
        start, size, end, last_reply = self.pos, 0, self.pos, None
        while end < len(self.events):
            ev = self.events[end]
            size += len(ev[1]) if ev[0] in ("you", "reply") else (len(str(ev[3])) // 3 + 60 if ev[0] == "call" else
                                                                  min(len(ev[2] or ""), 900) if ev[0] == "result" else 3000)
            end += 1
            if ev[0] == "reply":
                last_reply = ev[2]
                if size >= limit:
                    break
        if last_reply is None:  # (no reply yet past here: wait for one)
            return False
        while end < len(self.events) and self.events[end - 1][0] != "reply":
            end -= 1
        part_events = self.events[start:end]
        numbers = [ev[2] for ev in part_events if ev[0] == "reply"]
        a, b = numbers[0], numbers[-1]
        if self.proc is None:
            self.start_session()
        self.parts += 1
        self.part = {"k": self.parts, "a": a, "b": b, "end": end, "started": time.monotonic()}
        self.ref.jobs.put(("reader_part", {"k": self.parts, "a": a, "b": b, "events": part_events,
                                           "full": self.full, "glossary": self.glossary_only}, None))
        return False

    def instructions(self):
        """What the reader is (its system prompt: the same for every session of this chat, so it's cached once)."""
        seen = ", and the places Claude itself looked at that mention them" if self.full else ""
        return SIGNAL_READER_INTRO.format(
            root=self.ref.root, seen=seen, seen_tool=", the places Claude saw" if self.full else "",
            what=", what Claude ran and saw before replying ([Claude → Tool] lines, with the start of each "
                 "result under them; a subagent's work indented under the call that started it)" if self.full
            else "")

    def send(self, k, a, b, text):
        """The part, rendered by the worker (with the rules' answers for its names), to the session (main loop)."""
        if self.part is None or self.part["k"] != k or self.proc is None:
            return False
        if self.intro:  # (a new session: where it starts reading)
            first = a - 1 if self.glossary_only else getattr(self, "resume_from", 0)
            resume = SIGNAL_READER_FRESH if self.glossary_only else SIGNAL_READER_RESUME
            text = (resume.format(n=first) + "\n\n" if first else "") + text
            self.intro = False
        self.proc.user(text)
        self.ref.say(f"{self.model.capitalize()}: reading part {k} (replies {a} to {b} of {len(self.replies)})…")
        self.ref.reader_said("part", f"Part {k} · replies {a} to {b} of {len(self.replies)}")
        self.warm_spare()
        return False

    def start_session(self):
        """A session for the next part: the spare, if one was started (it's done its setup), else a new one."""
        self.proc, self.spare = self.spare or self.spawn(), None
        self.intro = True

    def spawn(self):
        cfg = os.path.join(self.ref.tool_socket_dir(), f"signal-mcp-{id(self)}.json")
        with open(cfg, "w") as f:
            json.dump({"mcpServers": {"signal": {"command": sys.executable,
                                                 "args": [signal_reader_mcp_path(), self.ref.tool_socket()]}}}, f)
        made = {}
        made["proc"] = ClaudeProc(self.on_msg, lambda code: self.on_exit(code, made.get("proc")), self.ref.root,
                                  self.model, "default", None, self.effort, extra=self.session_args(cfg),
                                  env=SIGNAL_READER_ENV)
        return made["proc"]

    def warm_spare(self):
        """Glossary-only history: start the next part's session now, so it's ready when this part is done (a new
        claude takes about a second to set itself up; an idle one costs nothing)."""
        if self.glossary_only and self.spare is None and not self.stopped and self.cost < self.budget and \
                self.part is not None and any(ev[0] == "reply" for ev in self.events[self.part["end"]:]):
            self.spare = self.spawn()

    def drop_spare(self):
        if self.spare is not None:
            self.spare.kill()
            self.spare = None

    def session_args(self, mcp_config):
        """A clean session, not a copy of your Claude Code: it runs as Baude's signal-reader agent (its instructions
        are the system prompt, not Claude Code's coding agent's), with only the tools it uses, Signal's MCP server
        and no other (none of yours, no claude.ai connectors), your user settings but not the project's (its hooks,
        agents, CLAUDE.md), no auto memory, no skills or commands. What's left is the same for every session of a
        chat, so a new one reads it from the cache."""
        agent = {"signal-reader": {"description": "Baude Signal's reader: links the names in a chat's replies",
                                   "prompt": self.instructions()}}
        settings = {"autoMemoryEnabled": False, "disableClaudeAiConnectors": True,
                    "pluginConfigs": {"cc-plugin-agents-md@builtin": {"options": {"instructionFiles": "claude-md"}}}}
        return ["--no-session-persistence", "--mcp-config", mcp_config, "--strict-mcp-config", "--max-turns", "80",
                "--agents", json.dumps(agent), "--agent", "signal-reader", "--tools", "Read,Grep,Glob,Bash",
                "--disable-slash-commands", "--setting-sources", "user", "--settings", json.dumps(settings)]

    def account(self, proc, msg):
        delta = spent_since(proc, msg)
        self.cost += delta
        return delta

    # ---- the session's messages (main loop)
    def on_msg(self, proc, msg):
        if proc is not self.proc:
            return False
        t = msg.get("type")
        if t == "assistant" and not msg.get("parent_tool_use_id"):
            self.ref.reader_said("assistant", msg.get("message", {}).get("content") or [])
            for c in msg.get("message", {}).get("content", []):
                if c.get("type") == "tool_use":
                    inp = c.get("input") or {}
                    if c.get("name") in ("Grep", "Glob", "Bash"):
                        self.searches.append(str(inp.get("pattern") or inp.get("command") or ""))
                    what = inp.get("name") or inp.get("pattern") or inp.get("command") or inp.get("file_path") or \
                        inp.get("table") or inp.get("column") or (f"{len(inp.get('decisions') or [])} decisions"
                                                                  if "decisions" in inp else "")
                    self.ref.say(f"{self.model.capitalize()}, part {self.part['k'] if self.part else '?'}: "
                                 f"{c.get('name', '').removeprefix('mcp__signal__')} {_first_line(str(what), 70)}")
        elif t == "user" and isinstance(content := msg.get("message", {}).get("content"), list):
            self.ref.reader_said("results", content)
        elif t == "control_request" and msg.get("request", {}).get("subtype") == "can_use_tool":
            self.permission(proc, msg)
        elif t == "result":
            delta, part = self.account(proc, msg), self.part
            if part is not None:
                self.pos, self.part = part["end"], None
                self.ref.note(f"{self.model.capitalize()}: part {part['k']} (replies {part['a']} to {part['b']}) read "
                              f"in {time.monotonic() - part['started']:.0f} s ({ref_money(delta)}; "
                              f"{ref_money(self.cost)} so far)")
                self.ref.reader_said("done", f"Part {part['k']} read in {time.monotonic() - part['started']:.0f} s "
                                             f"({ref_money(delta)}; {ref_money(self.cost)} so far)")
                if self.pos >= len(self.events):
                    self.ref.say(None)
                    self.drop_spare()  # (read to the end: a chat that goes on starts a session when it does)
                if self.glossary_only:  # (the next part gets a session of its own)
                    self.proc, self.intro = None, True
                    proc.close_stdin()
                self.schedule(50)
        return False

    def permission(self, proc, msg):
        req = msg["request"]
        name, inp = req.get("tool_name") or "", req.get("input") or {}
        ok = name.startswith("mcp__signal__") or name in ("Read", "Grep", "Glob", "LS") or \
            (name == "Bash" and ref_agent_bash_ok(inp.get("command", "")))
        if ok and not name.startswith("mcp__signal__") and ref_kubeconfig_touch(json.dumps(inp)):
            proc.respond(msg["request_id"], {"behavior": "deny", "message": "Signal's reader doesn't read kubeconfig "
                                             "files (they hold cluster credentials): mcp__signal__kube lists their "
                                             "contexts' names, clusters and namespaces."})
            self.ref.note(f"{self.model.capitalize()}: refused to read a kubeconfig ({name})")
            self.ref.reader_said("refused", f"Refused: reading a kubeconfig ({name})")
            return
        if ok:
            proc.respond(msg["request_id"], {"behavior": "allow", "updatedInput": inp})
        else:
            proc.respond(msg["request_id"], {"behavior": "deny", "message": "Signal's reader is read-only: use Grep, "
                                             "Glob, Read, read-only git commands, or the mcp__signal__ tools."})
            self.ref.note(f"{self.model.capitalize()}: refused {name} {_first_line(str(inp.get('command') or inp), 60)}")
            self.ref.reader_said("refused", f"Refused: {name} {_first_line(str(inp.get('command') or inp), 100)}")

    def on_exit(self, _code, proc=None):
        if proc is not None and proc is self.spare:  # (the spare died before it was needed: start another later)
            self.spare = None
            return False
        if proc is not None and proc is not self.proc:
            return False  # (a session that was done: glossary-only history ends one per part)
        if self.part is not None:  # (it ended mid-part: a new session reads that part again)
            self.ref.note(f"{self.model.capitalize()}: its session ended during part {self.part['k']}")
            self.part = None
        self.proc, self.intro = None, True
        if self.pos < len(self.events):
            self.schedule(2000)
        return False

    def busy(self):
        return not self.stopped and (self.part is not None or
                                     (self.pos < len(self.events) and self.cost < self.budget and
                                      any(ev[0] == "reply" for ev in self.events[self.pos:])))

    def stop(self):
        self.stopped = True
        if self.soon is not None:
            GLib.source_remove(self.soon)
            self.soon = None
        if self.proc is not None:
            self.proc.kill()
        self.drop_spare()
        self.proc, self.part = None, None

    def start(self):
        self.stopped = False
        self.schedule()


# what a model calls a kind -> the glossary's kind
REF_AGENT_KINDS = {"file": "file", "commit": "commit", "branch": "branch", "pr": "pr", "env": "env",
                   "constant": "constant", "function": "symbol", "class": "symbol", "field": "symbol",
                   "table": "symbol", "migration": "symbol", "route": "route", "config": "config", "k8s": "k8s",
                   "service": "service", "package": "package", "cli": "cli", "external": "external", "test": "symbol",
                   "tool": "tool", "subagent": "subagent", "skill": "skill"}
REF_RULE_KINDS = {"file", "commit", "branch", "pr", "env", "constant", "symbol", "route", "config", "k8s", "cli", "tool",
                  "subagent", "skill"}  # the rules can try these
_REF_AGENT_FIRST = re.compile(r"(git\s+(log|show|grep|ls-files|ls-tree|rev-parse|branch|status|diff|cat-file|"
                              r"for-each-ref|blame|describe)|rg|grep|ls|wc|find|head|tail|cat|file|stat|tree)\b")


def ref_agent_bash_ok(command):
    """Whether a Bash command from the model pass only reads: one allowed command (git's read-only ones, rg,
    ls, find without -exec / -delete...), piped only into head, tail, wc, grep, sort, uniq or cut."""
    command = re.sub(r"^\s*cd\s+[^;&|<>`$]+?\s*(&&|;)\s*", "", command)  # (a leading "cd <folder> &&" is fine)
    if re.search(r"[;&><`\n]|\$\(|\s-(exec|execdir|delete|ok|fprint)\b|--output\b", command):
        return False
    first, *rest = [p.strip() for p in command.split("|")]
    return bool(_REF_AGENT_FIRST.match(first)) and all(re.match(r"(head|tail|wc|grep|sort|uniq|cut)\b", p) for p in rest)


def ref_secretish(text):
    """Whether a name looks like a secret (an API key, a token, a private key): never kept as an entity."""
    return bool(re.search(r"\b(sk-ant-|sk-[A-Za-z0-9]{8}|gh[pousr]_|github_pat_|xox[abprs]-|AKIA[0-9A-Z]{8}|AIza)|"
                          r"-----BEGIN|\b[A-Za-z0-9+/_-]{36,}\b", text))


def ref_junk(surface, texts):
    """Whether a listed name isn't one: a number, or a plain lowercase word written only in prose (backticked,
    `app` may well be a name)."""
    if re.fullmatch(r"[\d.,:]+|[\d.]+\s*\w{0,3}", surface) or len(surface) < 2:
        return True
    return bool(re.fullmatch(r"[a-z]+", surface)) and not any(f"`{surface}`" in t for t in texts)


def ref_money(x):
    return f"${x:.3f}" if x < 1 else f"${x:.2f}"


def ref_surface_in(surface, texts):
    """surface as one of the replies writes it (a model may change the case), or None if none writes it."""
    if not surface:
        return None
    if any(surface in t for t in texts):
        return surface
    m = next((m for t in texts if (m := re.search(re.escape(surface), t, re.I))), None)
    return m.group(0) if m else None


def ref_sentence(texts, surface, width=170):
    """The text around the first place a reply writes surface (for the verifier)."""
    for t in texts:
        i = t.find(surface)
        if i >= 0:
            return re.sub(r"\s+", " ", t[max(0, i - width):i + len(surface) + width]).strip()
    return ""


KUBE_WORKLOADS = ("Deployment", "StatefulSet", "DaemonSet", "Rollout", "CronJob", "Job", "ReplicaSet", "Pod")
KUBE_ORDER = KUBE_WORKLOADS + ("Service", "Ingress", "HTTPRoute", "ConfigMap", "Secret", "ServiceAccount",
                               "HorizontalPodAutoscaler", "PodDisruptionBudget", "Namespace")  # (a name's objects, first first)
_KUBE_HINT = ("Deployment|StatefulSet|DaemonSet|CronJob|ConfigMap|Secret|Service|Ingress|Namespace|Pod|Job|Rollout|"
              "NodePool|ServiceAccount|PersistentVolumeClaim|HorizontalPodAutoscaler|HTTPRoute|deployment|statefulset|"
              "daemonset|cronjob|configmap|ingress|pod|namespace|(?:kube(?:config|ctl)? )?context")


def ref_kube_hint(before, after):
    """The kind a reply gives the backticked name between before and after ("the `x` Deployment", "Service `x`", "its
    `x` pods", "the `staging` context"): a Kubernetes kind, "context" (a kubeconfig's), or None."""
    m = re.match(rf"\s+({_KUBE_HINT})s?\b", after) or re.search(rf"\b({_KUBE_HINT})\s+$", before)
    if not m:
        return None
    word = m.group(1).split()[-1]
    return "context" if word == "context" else KUBE_KINDS.get(word.lower(), word)


def kube_pod_owners(name):
    """The workloads a pod's name may come from, likeliest first, as (kind or None, name): a CronJob's
    (<name>-<minutes since 1970>-<5>), a Deployment's (<name>-<ReplicaSet hash>-<5>), a Job's or a DaemonSet's
    (<name>-<5>), a StatefulSet's (<name>-<ordinal>). Kubernetes' suffixes have no vowels, nor 0, 1 or 3."""
    sfx = "[bcdfghjklmnpqrstvwxz2456789]"
    out = []
    if m := re.fullmatch(rf"(.+)-\d{{8,}}-{sfx}{{5}}", name):
        out.append(("CronJob", m.group(1)))
    if m := re.fullmatch(rf"(.+)-{sfx}{{6,10}}-{sfx}{{5}}", name):
        out.append(("Deployment", m.group(1)))
    if m := re.fullmatch(rf"(.+)-{sfx}{{5}}", name):
        out.append((None, m.group(1)))
    if m := re.fullmatch(r"(.+)-\d{1,4}", name):
        out.append(("StatefulSet", m.group(1)))
    return out


def ref_kubeconfig_touch(text):
    """Whether a tool call's input names a kubeconfig file (one Signal's reader may not read: it holds credentials)."""
    return bool(re.search(r"(?:^|[\s/'\"=~])\.kube(?:/|\b)|kubeconfig", text, re.I)) or \
        any(p in text for p in kubeconfig_paths())


# ------------------------------------------------ signal: language servers --
# A search finds where a name is defined, not which definition is meant: `previous.job_id` is a field of whatever
# class `previous` is, and only a type checker knows that. So code names also go to a language server: pyright for
# Python (one per package, with that package's .venv) and typescript-language-server for TypeScript and JavaScript
# (one per TypeScript install, so a package gets the version it builds with). Baude fetches them once, with your
# OK, at pinned versions into its own data folder, and starts one when a code name first needs it. Signal only
# asks them questions (a definition, a type, a file's symbols); it never asks them to edit, format or fix anything.

REF_LSP_PACKAGES = (("pyright", "1.1.414"), ("typescript-language-server", "5.3.0"), ("typescript", "5.9.3"))
REF_LSP_LANG = {".py": "python", ".pyi": "python", ".ts": "typescript", ".mts": "typescript", ".cts": "typescript",
                ".tsx": "typescriptreact", ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript",
                ".jsx": "javascriptreact"}
# the parts of each server Baude starts, under lsp_dir()/node_modules, and the command if one is on PATH instead
_REF_LSP_SERVERS = (("python", "BAUDE_LSP_PYTHON", "pyright", "langserver.index.js", "pyright-langserver"),
                    ("typescript", "BAUDE_LSP_TYPESCRIPT", "typescript-language-server", "lib/cli.mjs",
                     "typescript-language-server"))


def lsp_dir():
    """Where Baude keeps the language servers it fetched (an npm folder of its own)."""
    return os.path.join(_data_home(), "baude", "lsp")


def lsp_node():
    """Node.js, which runs both servers: on PATH, else the newest one nvm installed (a launcher's PATH has no nvm)."""
    if node := shutil.which("node"):
        return node
    import glob
    found = glob.glob(os.path.expanduser("~/.nvm/versions/node/v*/bin/node"))
    return max(found, key=lambda p: [int(x) for x in re.findall(r"\d+", p.rsplit("/node/v", 1)[-1])]) if found else None


def lsp_commands():
    """How to start each language server that's there: {"python": argv, "typescript": argv}. Baude's own (fetched
    at the pinned version) come first, then ones on PATH; BAUDE_LSP_PYTHON and BAUDE_LSP_TYPESCRIPT (a command
    line each) override both, as the tests do."""
    import shlex
    out, node, pins = {}, lsp_node(), dict(REF_LSP_PACKAGES)
    for lang, env, package, script, exe in _REF_LSP_SERVERS:
        mine = os.path.join(lsp_dir(), "node_modules", package)
        try:
            with open(os.path.join(mine, "package.json")) as f:
                version = json.load(f).get("version")
        except (OSError, ValueError):
            version = None
        if os.environ.get(env):
            out[lang] = shlex.split(os.environ[env])
        elif node and version == pins[package]:
            out[lang] = [node, os.path.join(mine, script), "--stdio"]
        elif shutil.which(exe) and version is None:  # (an older fetch of Baude's own is updated instead)
            out[lang] = [shutil.which(exe), "--stdio"]
    return out


def lsp_fetch():
    """Install the pinned servers into lsp_dir() with npm (a minute or so: run it in a thread of its own).
    Returns (ok, what happened)."""
    node = lsp_node()
    npm = os.path.join(os.path.dirname(node), "npm") if node else None
    if not npm or not os.path.exists(npm):
        return False, "they need Node.js (node and npm), which isn't installed"
    folder = lsp_dir()
    os.makedirs(folder, exist_ok=True)
    if not os.path.exists(os.path.join(folder, "package.json")):
        with open(os.path.join(folder, "package.json"), "w") as f:
            json.dump({"private": True, "description": "Language servers for Baude's Signal"}, f)
    env = {**os.environ, "PATH": os.path.dirname(node) + os.pathsep + os.environ.get("PATH", "")}
    try:
        p = subprocess.run([npm, "install", "--no-audit", "--no-fund", "--ignore-scripts", "--save-exact",
                            *(f"{name}@{version}" for name, version in REF_LSP_PACKAGES)],
                           cwd=folder, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=900)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"npm failed ({e})"
    if p.returncode:
        lines = (p.stderr or p.stdout or "").strip().splitlines()
        return False, f"npm failed: {_first_line(lines[-1] if lines else str(p.returncode), 200)}"
    return True, "fetched " + ", ".join(f"{name} {version}" for name, version in REF_LSP_PACKAGES)


def lsp_uri(path):
    return "file://" + urllib.parse.quote(os.path.abspath(path))


def lsp_path(uri):
    return urllib.parse.unquote(urllib.parse.urlparse(uri).path) if isinstance(uri, str) and uri.startswith("file:") \
        else None


def lsp_places(result):
    """(absolute path, 1-based line) of each Location or LocationLink in a definition's answer."""
    out = []
    for x in result if isinstance(result, list) else [result] if isinstance(result, dict) else []:
        rng = x.get("targetSelectionRange") or x.get("targetRange") or x.get("range") or {}
        path = lsp_path(x.get("targetUri") or x.get("uri"))
        if path and isinstance(rng.get("start"), dict):
            out.append((path, rng["start"].get("line", 0) + 1))
    return out


def ref_inside(path, folder):
    return path == folder or path.startswith(folder.rstrip(os.sep) + os.sep)


class LspServer:
    """One language server, over its stdin and stdout (JSON-RPC with Content-Length headers). A request waits for its
    answer; a reader thread answers the server's own requests (its settings) and follows its progress reports, which
    is how a TypeScript project says it has loaded."""

    def __init__(self, argv, root, kind, settings=None, options=None):
        self.root, self.kind, self.settings = root, kind, settings or {}
        self.lock, self.next_id, self.waiting = threading.Lock(), 0, {}
        self.opening, self.users = threading.Lock(), set()  # (several Signals' workers share it: RefCode)
        self.progress, self.loads = set(), 0  # work going on now; loads finished
        self.opened, self.dead, self.used = {}, False, time.monotonic()
        self.proc = subprocess.Popen(argv, cwd=root, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, start_new_session=True)
        threading.Thread(target=self._read, daemon=True, name=f"baude-lsp-{kind}").start()
        uri = lsp_uri(root)
        caps = {"window": {"workDoneProgress": True},
                "workspace": {"configuration": True, "workspaceFolders": True, "symbol": {}},
                "textDocument": {"synchronization": {}, "definition": {}, "typeDefinition": {},
                                 "documentSymbol": {"hierarchicalDocumentSymbolSupport": True}}}
        if self.request("initialize", {"processId": os.getpid(), "rootUri": uri, "capabilities": caps,
                                       "workspaceFolders": [{"uri": uri, "name": os.path.basename(root)}],
                                       "initializationOptions": options or {}}, timeout=60) is None:
            self.close()
            raise OSError("it didn't answer")
        self.notify("initialized", {})
        if self.settings:
            self.notify("workspace/didChangeConfiguration", {"settings": self.settings})

    def _send(self, msg):
        data = json.dumps(msg).encode()
        try:
            with self.lock:
                self.proc.stdin.write(b"Content-Length: %d\r\n\r\n" % len(data) + data)
                self.proc.stdin.flush()
            return True
        except (OSError, ValueError):
            self.dead = True
            return False

    def _read(self):
        out = self.proc.stdout
        try:
            while True:
                size = None
                while (line := out.readline()) not in (b"\r\n", b"\n"):
                    if not line:
                        return
                    key, _, value = line.partition(b":")
                    if key.strip().lower() == b"content-length":
                        size = int(value)
                msg = json.loads(out.read(size or 0))
                method, mid = msg.get("method"), msg.get("id")
                if method and mid is not None:  # the server asks: for its settings; anything else gets null
                    items = (msg.get("params") or {}).get("items") or []
                    result = [self._section(i.get("section")) for i in items] \
                        if method == "workspace/configuration" else None
                    self._send({"jsonrpc": "2.0", "id": mid, "result": result})
                elif method == "$/progress":
                    p = msg.get("params") or {}
                    kind = (p.get("value") or {}).get("kind")
                    if kind == "begin":
                        self.progress.add(p.get("token"))
                    elif kind == "end" and p.get("token") in self.progress:
                        self.progress.discard(p.get("token"))
                        self.loads += 1
                elif mid is not None and (w := self.waiting.pop(mid, None)):
                    w[1] = msg
                    w[0].set()
        except (OSError, ValueError, TypeError):
            pass
        finally:
            self.dead = True
            for w in list(self.waiting.values()):
                w[0].set()

    def _section(self, section):
        node = self.settings
        for key in section.split(".") if section else ():
            node = node.get(key) if isinstance(node, dict) else None
        return node

    def request(self, method, params, timeout=20):
        """The answer's result; None if it failed, timed out or the server has gone."""
        if self.dead:
            return None
        with self.lock:
            self.next_id += 1
            mid = self.next_id
        w = self.waiting[mid] = [threading.Event(), None]
        self.used = time.monotonic()
        if not self._send({"jsonrpc": "2.0", "id": mid, "method": method, "params": params}) \
                or not w[0].wait(timeout):
            self.waiting.pop(mid, None)
            self.notify("$/cancelRequest", {"id": mid})
            return None
        return None if w[1] is None or "error" in w[1] else w[1].get("result")

    def notify(self, method, params):
        if not self.dead:
            self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def open(self, path, lang):
        """Tell the server about a file (once, and again when it has changed). False if it can't be read."""
        with self.opening:  # (two Signals may ask about the same file at once)
            return self._open(path, lang)

    def _open(self, path, lang):
        try:
            mtime = os.stat(path).st_mtime_ns
            if self.opened.get(path) == mtime:
                return True
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read(Signal.BLOB_LIMIT)
        except OSError:
            return False
        if path in self.opened:
            self.notify("textDocument/didClose", {"textDocument": {"uri": lsp_uri(path)}})
            del self.opened[path]
        while len(self.opened) >= 40:  # (the server keeps open files in memory)
            old = next(iter(self.opened))
            self.notify("textDocument/didClose", {"textDocument": {"uri": lsp_uri(old)}})
            del self.opened[old]
        self.notify("textDocument/didOpen", {"textDocument": {"uri": lsp_uri(path), "languageId": lang,
                                                              "version": 1, "text": text}})
        self.opened[path] = mtime
        return True

    def settle(self):
        """After an empty answer from a TypeScript server: wait for the project it may be loading (it reports that
        as progress, starting a moment after a file opens), then True to ask again. False: nothing was loading."""
        if self.kind != "typescript":
            return False
        end = time.monotonic() + (8 if not self.loads else 1.5)
        while not self.progress and not self.dead and time.monotonic() < end:
            time.sleep(0.1)
        if not self.progress:
            return False
        end = time.monotonic() + 60
        while self.progress and not self.dead and time.monotonic() < end:
            time.sleep(0.1)
        return not self.dead

    def close(self):
        self.dead = True
        try:
            self.proc.kill()
            self.proc.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            pass


class RefCode:
    """A Signal's language servers (used on its worker thread): which one answers for a file, started the first time
    one is needed. They're shared by every Signal (servers, by package: a chat in a package and a chat in another
    folder of it ask the same one), each stopped when no Signal that used it is running (stop), or left idle; the
    least recently used is stopped when more than MAX run."""

    MAX, IDLE = 4, 900
    servers, failed, lock = {}, {}, threading.Lock()  # (every Signal's: key -> LspServer, key -> why it failed)

    def __init__(self, ref):
        self.ref, self.cmds, self.checked, self.mine = ref, {}, 0, set()  # (mine: the keys of the servers it used)

    def ready(self):
        """Whether any server is installed (looked at again every half minute)."""
        if time.monotonic() - self.checked > 30:
            self.cmds, self.checked = lsp_commands(), time.monotonic()
        return bool(self.cmds)

    def _spec(self, path):
        """(key, root, settings, init options, what it is, language id) of the server for a file; None if none."""
        lang = REF_LSP_LANG.get(os.path.splitext(path)[1].lower())
        kind = "python" if lang == "python" else "typescript" if lang else None
        if kind not in self.cmds:
            return None
        full = os.path.join(self.ref.root, path)
        return (*(self._python(full) if kind == "python" else self._typescript(full)), lang)

    def key(self, path):
        return (spec := self._spec(path)) and spec[0]

    def server(self, path):
        """(server, language id) for a file in the repository (a relative path); (None, None) if none can answer."""
        if (spec := self._spec(path)) is None:
            return None, None
        key, root, settings, options, what, lang = spec
        kind = key[0]
        with RefCode.lock:  # (another Signal's worker may be starting the same one)
            srv = self.servers.get(key)
            if srv is not None and srv.dead:
                del self.servers[key]
                self.failed[key] = "it stopped"
                self.ref._tell(entry=f"{what} stopped")
                srv = None
            if srv is None:
                if key in self.failed:
                    return None, None
                self._trim()
                self.ref._tell(activity=f"Starting {what}…")
                t0 = time.monotonic()
                try:
                    srv = LspServer(self.cmds[kind], root, kind, settings, options)
                except OSError as e:
                    self.failed[key] = str(e)
                    self.ref._tell(entry=f"Couldn't start {what}: {e}")
                    return None, None
                srv.what = what
                self.servers[key] = srv
                self.ref._tell(entry=f"Started {what} in {time.monotonic() - t0:.1f} s")
            elif self not in srv.users:
                self.ref._tell(entry=f"Using {what}, already running")
            srv.users.add(self)
            self.mine.add(key)
        srv.used = time.monotonic()
        return srv, lang

    def _python(self, full):
        """pyright for the package a file is in (the nearest folder with a pyproject.toml, setup.py or setup.cfg),
        with that package's virtual environment, else the nearest one above it."""
        root, rel = self.ref.root, lambda d: os.path.relpath(d, self.ref.root)  # noqa: E731
        d, pkg, python = os.path.dirname(full), None, None
        while ref_inside(d, root):
            if pkg is None and any(os.path.exists(os.path.join(d, m)) for m in ("pyproject.toml", "setup.py", "setup.cfg")):
                pkg = d
            if pkg is not None and (python := next((p for v in (".venv", "venv") if os.path.exists(
                    p := os.path.join(d, v, "bin", "python"))), None)):
                break
            if d == root:
                break
            d = os.path.dirname(d)
        pkg = pkg or root
        analysis = {"typeCheckingMode": "off", "diagnosticMode": "openFilesOnly", "autoSearchPaths": True,
                    "useLibraryCodeForTypes": True}
        settings = {"python": {"analysis": analysis, **({"pythonPath": python} if python else {})}}
        venv = f"with {rel(os.path.dirname(os.path.dirname(python)))}" if python else "without a virtual environment"
        return ("python", pkg, python), pkg, settings, {}, f"pyright for {rel(pkg)} ({venv})"

    def _typescript(self, full):
        """typescript-language-server with the TypeScript the file's package installed (the nearest
        node_modules/typescript that has a tsserver: TypeScript 7 has none), else the one Baude fetched."""
        root, d, tsserver = self.ref.root, os.path.dirname(full), None
        while ref_inside(d, root):
            if os.path.exists(p := os.path.join(d, "node_modules", "typescript", "lib", "tsserver.js")):
                tsserver = p
                break
            if d == root:
                break
            d = os.path.dirname(d)
        if tsserver is None:
            d, own = root, os.path.join(lsp_dir(), "node_modules", "typescript", "lib", "tsserver.js")
            tsserver = own if os.path.exists(own) else None
        try:
            with open(os.path.join(os.path.dirname(os.path.dirname(tsserver)), "package.json")) as f:
                version = f" {json.load(f).get('version', '')}"
        except (OSError, ValueError, TypeError):
            version = ""
        options = {"disableAutomaticTypingAcquisition": True, "tsserver": {"logVerbosity": "off"}}
        if tsserver:
            options["tsserver"]["path"] = tsserver
        where = os.path.relpath(d, root)
        return ("typescript", tsserver), d, None, options, f"typescript-language-server for {where} (TypeScript{version})"

    def _trim(self):
        now = time.monotonic()  # (RefCode.lock held)
        for key, srv in list(self.servers.items()):
            if srv.dead or now - srv.used > self.IDLE:
                self.servers.pop(key).close()
        while len(self.servers) >= self.MAX:
            self.servers.pop(min(self.servers, key=lambda k: self.servers[k].used)).close()

    def _ask(self, path, method, params):
        """A request about a file (opened first); a TypeScript project still loading is waited for."""
        srv, lang = self.server(path)
        full = os.path.join(self.ref.root, path)
        if srv is None or not srv.open(full, lang):
            return None, None
        params = {"textDocument": {"uri": lsp_uri(full)}, **params}
        res = srv.request(method, params, timeout=40 if not srv.loads else 15)
        if not res and srv.settle():
            res = srv.request(method, params, timeout=15)
        return srv, res

    def definitions(self, path, line, character, method="textDocument/definition"):
        """Where the server says the name at a position is defined (or, with textDocument/typeDefinition, its type):
        [(absolute path, 1-based line)]. character counts UTF-16 units, as LSP does (lsp_col)."""
        srv, res = self._ask(path, method, {"position": {"line": line - 1, "character": character}})
        return lsp_places(res)

    def symbols(self, path):
        """A file's symbols: [(name, kind, 1-based line, character, the names of the symbols around it)]."""
        out = []

        def walk(items, parents):
            for s in items if isinstance(items, list) else []:
                if not isinstance(s, dict) or "name" not in s:
                    continue
                at = (s.get("selectionRange") or s.get("range") or (s.get("location") or {}).get("range") or {}) \
                    .get("start") or {}
                around = [s["containerName"]] if "location" in s and s.get("containerName") else parents
                out.append((s["name"], s.get("kind"), at.get("line", 0) + 1, at.get("character", 0), around))
                walk(s.get("children"), parents + [s["name"]])
        walk(self._ask(path, "textDocument/documentSymbol", {})[1], [])
        return out

    def workspace(self, path, name):
        """The symbols named exactly name in the project of a file (its package): [(abs path, line, kind, container)]."""
        srv, lang = self.server(path)
        if srv is None or not srv.open(os.path.join(self.ref.root, path), lang):  # (a TypeScript project loads)
            return []
        res = srv.request("workspace/symbol", {"query": name}, timeout=40 if not srv.loads else 15)
        if not res and srv.settle():
            res = srv.request("workspace/symbol", {"query": name}, timeout=15)
        out = []
        for s in res if isinstance(res, list) else []:
            loc = s.get("location") or {}
            path_, start = lsp_path(loc.get("uri")), (loc.get("range") or {}).get("start")
            if s.get("name") == name and path_ and start:
                out.append((path_, start.get("line", 0) + 1, s.get("kind"), s.get("containerName") or ""))
        return out

    def running(self):
        """This Signal's servers that are running (another's it used too)."""
        return [srv.what for key, srv in list(self.servers.items()) if key in self.mine and not srv.dead]

    def stop(self):
        """This Signal is done with its servers: each stops if no other Signal that used it is running; those that
        failed may be tried again."""
        with RefCode.lock:
            for key in self.mine:
                srv = self.servers.get(key)
                if srv is not None:
                    srv.users.discard(self)
                    if not srv.users:
                        self.servers.pop(key, None)
                        try:
                            srv.close()
                        except OSError:
                            pass
                self.failed.pop(key, None)
            self.mine.clear()


def lsp_col(text, i):
    """A character index in a line as LSP counts it: UTF-16 code units."""
    return len(text[:i].encode("utf-16-le")) // 2


def ref_code_index(text, needle):
    """Where needle is used as code in a line: not in a comment or a string, not part of a longer name. None if not."""
    if text.lstrip().startswith(("#", "//", "*", "/*", '"""', "'''")):
        return None
    for m in re.finditer(re.escape(needle), text):
        i, before, after = m.start(), text[:m.start()], text[m.end():m.end() + 1]
        if (before[-1:].isalnum() or before[-1:] in ("_", "$")) and not needle.startswith("."):
            continue
        if after.isalnum() or after in ("_", "$"):
            continue
        if before.count('"') % 2 or before.count("'") % 2 or before.count("`") % 2 or re.search(r"(^|\s)(#|//)", before):
            continue
        return i
    return None


def ref_code_sites(hits, needle, touched=(), limit=4):
    """The places code uses needle, for a language server to look at: [(path, line, text, index)], one per file,
    the session's own files first and tests last."""
    out, seen = [], set()
    for path, n, text in sorted(hits, key=lambda h: (h[0] not in touched, ref_is_test(h[0]), h[0], h[1])):
        if path in seen or os.path.splitext(path)[1].lower() not in REF_LSP_LANG:
            continue
        if (i := ref_code_index(text, needle)) is not None:
            seen.add(path)
            out.append((path, n, text, i))
            if len(out) >= limit:
                break
    return out


_REF_PY_DEFS = {}  # the last few texts ref_py_scope parsed: their classes and functions (None: it doesn't parse)
_REF_PY_LOCK = threading.Lock()  # (every Signal's worker uses it: one's taking a text out as another puts one in)


def ref_py_scope(text, line):
    """Where a line of Python is, by Python's own parser (no server needed): what it's directly in ("class",
    "function" or "module"; None if it doesn't parse) and the classes around it, outermost first. A text is parsed
    once (_REF_PY_DEFS): parsed again for each of a name's places (60 of them, say), baude.py's parse held Python's
    lock a tenth of a second each time, and the window waited."""
    import ast
    with _REF_PY_LOCK:
        known = text in _REF_PY_DEFS
        defs = _REF_PY_DEFS.get(text)
    if not known:
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError):
            defs = None
        else:
            defs = [(n.lineno, n.end_lineno or n.lineno, isinstance(n, ast.ClassDef), n.name) for n in ast.walk(tree)
                    if isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))]
        with _REF_PY_LOCK:
            _REF_PY_DEFS[text] = defs
            while len(_REF_PY_DEFS) > 8:
                _REF_PY_DEFS.pop(next(iter(_REF_PY_DEFS)))
    if defs is None:
        return None, []
    around = sorted((d for d in defs if d[0] < line <= d[1]), key=lambda d: d[0])
    scope = "module" if not around else "class" if around[-1][2] else "function"
    return scope, [name for _first, _last, is_class, name in around if is_class]


def ref_same_name(a, b):
    """Whether an object's name and a class's are the same name (dlg and Dlg, shell_api and ShellApi)."""
    return a.replace("_", "").lower() == b.replace("_", "").lower()


# -------------------------------------------------------------- artifacts --
# Claude publishes pages with its Artifact tool ("Published <file> at https://claude.ai/artifact/<id>
# (Version 1 ...) Icon: "chart"."). baude shows them as cards with a real title and open/copy.

ARTIFACT_URL = re.compile(r"https://claude\.ai/(?:code/)?artifact/[A-Za-z0-9_-]+")
ARTIFACT_ICONS = {"chart": "x-office-spreadsheet-symbolic", "dashboard": "x-office-spreadsheet-symbolic",
                  "table": "x-office-spreadsheet-symbolic", "calendar": "x-office-calendar-symbolic",
                  "map": "baude-map-symbolic", "code": "utilities-terminal-symbolic",
                  "doc": "x-office-document-symbolic", "document": "x-office-document-symbolic",
                  "slides": "x-office-presentation-symbolic", "deck": "x-office-presentation-symbolic",
                  "image": "image-x-generic-symbolic", "music": "audio-x-generic-symbolic",
                  "game": "applications-games-symbolic", "mail": "mail-unread-symbolic"}


def html_title(path):
    """The <title> of a local HTML file (the Artifact tool requires pages to have one),
    or the first heading of a Markdown file."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            head = f.read(200_000)
    except (OSError, TypeError):
        return None
    m = re.search(r"<title[^>]*>(.*?)</title>", head, re.S | re.I)
    if m:
        return html.unescape(" ".join(m.group(1).split())) or None
    m = re.search(r"^#\s+(.+)$", head, re.M)
    return m.group(1).strip() if m else None


def artifact_from_tool(inp, result_text):
    """Artifact info from an Artifact tool call and its result, or None if nothing was published."""
    m = re.search(r"Published (\S+) at (" + ARTIFACT_URL.pattern + ")", result_text or "")
    url = m.group(2) if m else None
    if url is None and inp.get("type_url") and (m2 := ARTIFACT_URL.search(result_text or "")):
        url = m2.group(0)  # created from a type: the new artifact's link
    if url is None:
        return None
    path = (m.group(1) if m else None) or inp.get("file_path")
    version = re.search(r"\(Version (\d+)", result_text or "")
    icon = re.search(r'Icon: "([\w-]+)"', result_text or "")
    title = (inp.get("title") or html_title(path)
             or (humanize(os.path.splitext(os.path.basename(path))[0]).capitalize() if path else None))
    return {"url": url, "title": title or "Artifact", "path": path, "icon": (icon.group(1) if icon else inp.get("icon")),
            "version": int(version.group(1)) if version else None, "description": inp.get("description")}


class ArtifactChip(Gtk.Box):
    """[icon] Title / claude.ai/artifact/... · v2   [Open] [Copy link] — click anywhere to open."""

    def __init__(self, info):
        super().__init__(spacing=10, halign=Gtk.Align.START)
        self.add_css_class("artifact-chip")
        self.info = info
        ensure_icons()
        icon = Gtk.Image.new_from_icon_name(themed_icon(ARTIFACT_ICONS.get(info.get("icon") or ""), globe_icon()))
        icon.add_css_class("artifact-icon")  # (the icons' size: Settings → Size)
        self.append(icon)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
        self.title = Gtk.Label(label=info.get("title") or "Artifact", xalign=0, ellipsize=Pango.EllipsizeMode.END,
                               max_width_chars=48)
        self.title.add_css_class("heading")
        sub = info["url"].replace("https://", "")
        if info.get("version"):
            sub += f" · v{info['version']}"
        self.subtitle = Gtk.Label(label=sub, xalign=0, ellipsize=Pango.EllipsizeMode.MIDDLE, max_width_chars=48)
        self.subtitle.add_css_class("dim-label")
        self.subtitle.add_css_class("caption")
        text.append(self.title)
        text.append(self.subtitle)
        self.append(text)
        self.open_btn = Gtk.Button(label="Open", valign=Gtk.Align.CENTER, tooltip_text=info["url"])
        self.open_btn.connect("clicked", lambda *_: self.open())
        self.copy_btn = Gtk.Button(icon_name="edit-copy-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Copy link")
        self.copy_btn.add_css_class("flat")
        self.copy_btn.connect("clicked", lambda *_: (self.get_clipboard().set(info["url"]), toast(self, "Link copied")))
        self.append(self.open_btn)
        self.append(self.copy_btn)
        click = Gtk.GestureClick()
        click.connect("released", lambda *_: self.open())
        text.add_controller(click)
        icon.add_controller(Gtk.GestureClick())
        pointers.hand(text)
        self.set_tooltip_text(info.get("description") or info["url"])

    def open(self):
        Gtk.UriLauncher(uri=self.info["url"]).launch(self.get_root(), None, None)


# ------------------------------------------------------------- tool views --
# Tool inputs are shown as what they mean (a command, a diff, a path), never as raw JSON.

LANG_BY_EXT = {".py": "python", ".js": "javascript", ".ts": "typescript", ".sh": "bash", ".rs": "rust",
               ".go": "go", ".json": "json", ".md": "markdown", ".html": "html", ".css": "css",
               ".c": "c", ".h": "c", ".cpp": "cpp", ".java": "java", ".rb": "ruby", ".toml": "toml",
               ".yaml": "yaml", ".yml": "yaml", ".sql": "sql", ".tsx": "tsx", ".jsx": "jsx"}


def _first_line(v, limit=120):
    v = str(v).strip()
    return (v.splitlines()[0] if v else "")[:limit]


def summarize_tool(name, inp):
    """One line for a tool card header: what the call is for (Claude's description), or failing that,
    what it touches (a path, a pattern, a URL). A command without a description gets nothing."""
    if not isinstance(inp, dict):
        return ""
    if isinstance(inp.get("command"), str) and not (inp.get("description") or "").strip():
        return ""  # nothing rather than our own words: a description on this line is always Claude's
    for key in ("description", "command", "file_path", "notebook_path", "pattern", "url", "query",
                "prompt", "path", "skill"):
        if isinstance(inp.get(key), str) and inp[key].strip():
            return _first_line(inp[key])
    return ""


def _path_label(path, extra=""):
    lbl = sel_label(f"<tt>{html.escape(path)}</tt>" + (f"  <span alpha='60%'>{html.escape(extra)}</span>"
                                                        if extra else ""))
    lbl.add_css_class("tool-path")
    return lbl


def _value_widget(v, depth=0):
    """A readable widget for any JSON value: nested objects become indented rows."""
    if isinstance(v, str) and "\n" in v:
        return CodeBlock(v)
    if isinstance(v, bool):
        return sel_label("yes" if v else "no")
    if isinstance(v, (str, int, float)):
        return sel_label(html.escape(str(v)))
    if isinstance(v, list) and all(isinstance(x, (str, int, float, bool)) for x in v):
        return sel_label(html.escape(", ".join(str(x) for x in v)))
    if isinstance(v, list) and all(isinstance(x, dict) and len(x) == 1 for x in v):
        # [{"login": "naza"}, {"login": "bob"}] -> naza, bob
        return sel_label(html.escape(", ".join(str(next(iter(x.values()))) for x in v)))
    if isinstance(v, dict) and depth < 3:
        return _kv_rows(v, depth=depth + 1) or sel_label("—")
    if isinstance(v, list) and depth < 3:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        for x in v:
            box.append(_value_widget(x, depth + 1))
        return box
    return sel_label(html.escape(json.dumps(v, ensure_ascii=False)), mono=True)


def _kv_rows(inp, skip=(), depth=0):
    grid = Gtk.Grid(column_spacing=12, row_spacing=4)
    row = 0
    for k, v in inp.items():
        if k in skip or v in (None, "", [], {}):
            continue
        key = Gtk.Label(label=humanize(k), xalign=1, yalign=0)
        key.add_css_class("dim-label")
        val = _value_widget(v, depth)
        val.set_hexpand(True)
        grid.attach(key, 0, row, 1, 1)
        grid.attach(val, 1, row, 1, 1)
        row += 1
    return grid if row else None


def humanize(identifier):
    """snake_case / camelCase / kebab-case -> 'words like this'."""
    words = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", identifier).replace("_", " ").replace("-", " ")
    return " ".join(words.split()).lower()


MCP_SERVER_NAMES = {"github": "GitHub", "gitlab": "GitLab", "gmail": "Gmail", "gdrive": "Google Drive",
                    "google_drive": "Google Drive", "gcal": "Google Calendar", "slack": "Slack",
                    "notion": "Notion", "linear": "Linear", "jira": "Jira", "figma": "Figma",
                    "postgres": "Postgres", "sqlite": "SQLite", "playwright": "Playwright", "sentry": "Sentry"}


def tool_display(name):
    """(friendly title, source or None) for a tool name. MCP tools are named
    mcp__<server>__<tool>; the CLI gives nothing friendlier, so derive it:
    mcp__github__create_issue -> ("Create issue", "GitHub")
    mcp__claude_ai_Slack__slack_send_message -> ("Send message", "Slack")."""
    if not name.startswith("mcp__") or name.count("__") < 2:
        return name, None
    _, server, tool = name.split("__", 2)
    server = re.sub(r"^(claude_ai_|plugin_[^_]+_)", "", server)
    if re.fullmatch(r"[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}", server):
        source = "MCP"  # an unnamed connector: only an id
    else:
        source = MCP_SERVER_NAMES.get(server.lower()) or " ".join(
            w[:1].upper() + w[1:] for w in humanize(server).split())
    prefix = server.lower() + "_"
    if tool.lower().startswith(prefix):  # slack_send_message on the Slack server
        tool = tool[len(prefix):]
    title = humanize(tool)
    return (title[:1].upper() + title[1:]) or name, source


def tool_input_view(name, inp):
    """A readable rendering of a tool call's input."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    if not isinstance(inp, dict) or not inp:
        return box
    add = box.append
    base = name.split("__")[-1] if name.startswith("mcp__") else name
    # the header already shows the description, so the views don't repeat it
    if base in ("Bash", "BashOutput", "PowerShell") and "command" in inp:
        shell = "bash" if base != "PowerShell" else "powershell"
        add(CodeBlock(inp["command"], shell, syntax=shell))
        rest = _kv_rows(inp, skip=("command", "description"))
    elif base == "Edit" and "file_path" in inp:
        add(_path_label(inp["file_path"], "all occurrences" if inp.get("replace_all") else ""))
        add(diff_block(inp.get("old_string"), inp.get("new_string"), inp["file_path"]))
        rest = None
    elif base == "MultiEdit" and "file_path" in inp:
        add(_path_label(inp["file_path"], f"{len(inp.get('edits') or [])} edits"))
        for e in inp.get("edits") or []:
            add(diff_block(e.get("old_string"), e.get("new_string"), inp["file_path"]))
        rest = None
    elif base == "Write" and "file_path" in inp:
        content = inp.get("content") or ""
        lines = content.splitlines()
        add(_path_label(inp["file_path"], f"{len(lines)} lines"))
        shown = "\n".join(lines[:2000]) + (f"\n… {len(lines) - 2000} more lines" if len(lines) > 2000 else "")
        add(CodeBlock(shown, LANG_BY_EXT.get(os.path.splitext(inp["file_path"])[1], ""), path=inp["file_path"]))
        rest = None
    elif base == "Read" and "file_path" in inp:
        span = ""
        if inp.get("offset") or inp.get("limit"):
            start = inp.get("offset") or 1
            span = f"lines {start}–{start + inp['limit'] - 1}" if inp.get("limit") else f"from line {start}"
        add(_path_label(inp["file_path"], span))
        rest = _kv_rows(inp, skip=("file_path", "offset", "limit"))
    elif base in ("Grep", "Glob") and "pattern" in inp:
        grep = base == "Grep"  # (a regex drawn as one, and as it's written: a label's text, never markup or markdown)
        add(CodeBlock(inp["pattern"], "regex" if grep else "glob", syntax="regex" if grep else None))
        rest = _kv_rows(inp, skip=("pattern",))
    elif base == "WebFetch" and "url" in inp:
        add(sel_label(f'<a href="{html.escape(inp["url"])}">{html.escape(inp["url"])}</a>'))
        if inp.get("prompt"):
            add(MarkdownView(inp["prompt"]))
        rest = None
    elif base in ("Task", "Agent") and "prompt" in inp:
        add(MarkdownView(inp["prompt"]))
        rest = _kv_rows(inp, skip=("prompt", "description"))
    elif base == "TodoWrite" and isinstance(inp.get("todos"), list):
        for t in inp["todos"]:
            mark = {"completed": "☑", "in_progress": "◐"}.get(t.get("status"), "☐")
            text = html.escape(t.get("content") or t.get("activeForm") or "")
            add(sel_label(f"{mark}  <s>{text}</s>" if t.get("status") == "completed" else f"{mark}  {text}"))
        rest = None
    else:
        rest = _kv_rows(inp)
    if rest:
        add(rest)
    return box


TASK_ENDINGS = {"completed": "finished", "failed": "failed", "killed": "stopped", "error": "failed"}

# What a restart waits for: a command running (real work, a process of its own), not the model writing (a turn cut short
# there is resumed from its transcript, and said so: show_cut). A call of one of these till its result (a subagent's
# too), and a background task that isn't a subagent till its end.
COMMAND_TOOLS = ("Bash", "PowerShell")
CUT_CONTINUE = "Continue where you left off: a restart cut your last turn short."


def command_what(inp):
    """A command call's input, as a restart's wait says it: its description, else its command's first line."""
    what = (inp or {}).get("description") or ((inp or {}).get("command") or "").strip().splitlines()[:1]
    what = what if isinstance(what, str) else (what[0] if what else "a command")
    return what if len(what) <= 80 else what[:79] + "…"


def track_commands(commands, msg):
    """commands ({id: what}) as claude's msg leaves them (track_commands of a Desktop chat's claude left mid-turn)."""
    t, content = msg.get("type"), (msg.get("message") or {}).get("content")
    if t == "assistant" and isinstance(content, list):
        for b in content:
            if isinstance(b, dict) and b.get("type") == "tool_use" and \
                    str(b.get("name") or "").split("__")[-1] in COMMAND_TOOLS:
                commands[b.get("id")] = command_what(b.get("input"))
    elif t == "user" and isinstance(content, list):
        for b in content:
            if isinstance(b, dict) and b.get("type") == "tool_result":
                commands.pop(b.get("tool_use_id"), None)
    elif t == "system" and msg.get("subtype") == "task_started" and msg.get("task_type") != "local_agent":
        commands[f"task:{msg.get('task_id')}"] = msg.get("description") or "a background task"
    elif t == "system" and msg.get("subtype") == "task_notification":
        commands.pop(f"task:{msg.get('task_id')}", None)
    elif t == "result":  # (a turn's calls are done by its end: what's left running is a background task)
        for k in [k for k in commands if not str(k).startswith("task:")]:
            commands.pop(k)


def api_error_label(text):
    """What Claude Code says in place of a reply that didn't come ("API Error: …", or a synthetic message): in the
    colour a failed tool call's is (Adwaita's error), its text where a tool call's starts (api-error: widgets.CSS), and
    "API Error" in bold."""
    m = re.match(r"(API Error)(.*)", text or "", re.S)
    markup = f"<b>{m.group(1)}</b>{html.escape(m.group(2))}" if m else html.escape(text or "")
    lbl = sel_label(markup, css="error")
    lbl.add_css_class("api-error")
    return lbl


def task_note_widget(note, card=None, on_jump=None):
    """A background task's ending whose card isn't in the chat (it started before what's drawn of it), as one quiet
    line: Bash · <its description> — finished (exit 0), with no mark before it; a failure all in the colour a failed
    tool call's border is. Never the raw command or XML. Its card drawn after all (an earlier part of the chat, drawn
    later): row.link_card(card, on_jump) names it as its card does, the description a link back to it (on_jump(card))."""
    status = note.get("status") or "completed"
    row = Gtk.Box(halign=Gtk.Align.START)
    row.add_css_class("task-note")
    summary = html.unescape(note.get("summary") or "")
    ending = TASK_ENDINGS.get(status, status)
    if (code := re.search(r"exit code (\d+)", summary)) is not None:
        ending += f" (exit {code.group(1)})"
    label = Gtk.Label(xalign=0, wrap=True, selectable=True)
    label.add_css_class("error" if status in ("failed", "killed", "error") else "dim-label")
    label.set_tooltip_text(summary or None)

    def link_card(card, on_jump=None):
        inp = card.input if card is not None else {}
        if card is not None:
            what = card.base
        else:
            what = "Agent" if note.get("task_type") == "local_agent" or summary.startswith(("Agent", "Subagent")) \
                else "Background command"
        desc = inp.get("description") or note.get("description")
        if not desc:  # no description: the command's first line, or what the summary quotes
            quoted = re.match(r'[^"]*"(.*)"', summary, re.S)
            desc = (inp.get("command") or (quoted.group(1) if quoted else summary)).strip().splitlines()[0:1]
            desc = desc[0] if desc else "a background task"
        if len(desc) > 80:
            desc = desc[:79] + "…"
        text = html.escape(desc)
        if card is not None and on_jump is not None:
            text = f'<a href="card">{text}</a>'
            label.connect("activate-link", lambda *_: (on_jump(card), True)[1])
        label.set_markup(f"<b>{html.escape(what)}</b> · {text} — {html.escape(ending)}")
    link_card(card, on_jump)
    row.link_card = link_card
    row.append(label)
    return row


class StatusSlot(Gtk.Box):
    """A fixed 16px place for a status icon whose content changes (spinner -> ✓/✗).
    Rows keep this one widget: swapping prefixes in and out of an Adw row is unreliable."""

    def __init__(self, state="running"):
        super().__init__(valign=Gtk.Align.CENTER)
        self.state, self.icon = None, None
        self.set_state(state)

    def set_state(self, state):
        if state == self.state:
            return
        if self.icon is not None:
            self.remove(self.icon)
        self.state, self.icon = state, _status_icon(state)
        self.append(self.icon)


class ThinkingView(Gtk.Box):
    """Claude's narration between steps. Current models' thinking arrives summarized: a sentence
    or two ("The workspace and its 7 default apps are installing… I'll upgrade it once that's done"),
    which Claude Code and Claude Desktop show in the conversation, dimmed. That's what this is:
    visible text, not a folded "thinking…" (the model's raw reasoning comes as empty, signed
    blocks, which are never drawn)."""

    def __init__(self, text=""):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.add_css_class("thinking")
        self.md = MarkdownView(text)
        Gtk.Box.append(self, self.md)  # (append() itself takes streamed text, like MarkdownView's)

    def append(self, delta):  # streaming deltas, like MarkdownView
        self.md.append(delta)

    def finish(self):
        self.md.finish()

    def has_text(self):
        return bool(self.md.text.strip())


# Claude Code's whole result for an edit or a write that went through: the path and that it worked, and maybe that
# the file is in its context. The card says all that already, in its name, its summary and its border. (Anything more,
# a note that the file changed on disk, say, isn't matched: it's shown.)
FILE_WRITTEN = re.compile(r"(?:The file (?:(?! \()[^\n])+ has been updated successfully\."
                          r"|File created successfully at: (?:(?! \()[^\n])+)"
                          r"(?:\s+\(file state is current in your context[^()\n]*\))?")
# (and a Skill's, that it started: the card's name and summary say which; what it was given is its Input)
SKILL_LAUNCHED = re.compile(r"Launching skill: [^\n]+")


def said_nothing(base, text, is_error):
    """Is a call's whole result only that it went through (an edit or write made, a skill started): no Output to
    open, since the card says that already?"""
    text = (text or "").strip()
    if is_error:
        return False
    return base in ("Edit", "MultiEdit", "Write") and bool(FILE_WRITTEN.fullmatch(text)) or \
        base == "Skill" and bool(SKILL_LAUNCHED.fullmatch(text))


class ToolCard(Gtk.Box):
    """One tool call. state: running -> done | error, or running -> background -> done | error
    when the CLI runs it as a background task (its result only says it started)."""

    def __init__(self, name, tool_id):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.add_css_class("tool-card")
        TOOL_NAMES.add(name)  # (a tool this chat called: Signal can name it)
        self.name, self.tool_id, self.partial_json, self.input = name, tool_id, "", {}
        self.state, self.task, self.decision, self.listeners = "running", None, None, []
        self.artifact, self.asking, self.opened_to_ask = None, None, False
        # one line:  Name [Command] [Output]  summary (cut off with …)        [spinner, while it runs]
        # and under it, what those open: what was asked, what came back (once there is some)
        grid = self.grid = Gtk.Grid()
        self.line = Gtk.Box(spacing=8)
        self.status = None  # (_card_status)
        self.title = Gtk.Label(xalign=0)
        self.header = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, hexpand=True, selectable=True)
        self.header.set_can_focus(False)  # selectable for copying, but not a tab stop in every card
        toggles = self.toggles = Gtk.Box(spacing=4)
        for w in (self.title, toggles, self.header):
            self.line.append(w)
        grid.attach(self.line, 0, 0, 1, 1)
        self.append(grid)
        base = self.base = name.split("__")[-1]
        # (a Write's is the whole file it wrote, not a change: Input)
        self.input_toggle = Disclosure(toggles, grid, 1, "Command" if base in ("Bash", "BashOutput", "PowerShell")
                                       else "Prompt" if base in ("Task", "Agent")
                                       else "Diff" if base in ("Edit", "MultiEdit") else "Input", button=True)
        self.output_toggle = Disclosure(toggles, grid, 2, "Output", button=True)
        self.output_toggle.set_visible(False)  # until there is output
        self.show_status()
        # what's folded away is built when first opened (a long session has thousands of these), and so are the
        # boxes it goes in: the input's, the output's (media_box, children, result_box: made when first used)
        self.input_box, self._media_box, self._output = None, None, None
        self.input_stale, self.pending_output = True, []
        self.input_toggle.on_expand.append(self.build_input)
        self.output_toggle.on_expand.append(self.build_output)
        self.set_input({})

    # ---- state
    def set_state(self, state):
        self.state = state
        self.show_status()
        if state == "error":
            self.add_css_class("error")
            self.output_toggle.set_label("Error")
        self.refresh_header()
        for fn in self.listeners:
            fn(self)

    def show_status(self):
        if self.status is not None:
            self.line.remove(self.status)
        self.status = _card_status(self.state) if self.asking is None else None  # (waiting on you: not running)
        if self.status is not None:
            self.line.append(self.status)

    def ask(self, buttons):
        """Asked whether to allow it, here in the card: what it would run, opened, and under it buttons (Allow, Deny…).
        It's waiting on you then, not running: no spinner, the border in the colour of waiting."""
        self.answered()  # (asked again before an answer: the earlier buttons go)
        self.asking = buttons
        self.add_css_class("asking")
        self.show_status()
        if self.input_toggle.get_visible() and not self.input_toggle.get_expanded():
            self.opened_to_ask = True
            self.input_toggle.set_expanded(True)
        buttons.set_margin_top(8)
        self.grid.attach(buttons, 0, 4, 1, 1)
        for fn in self.listeners:
            fn(self)

    def answered(self):
        """The ask is answered (or can't be any more): the buttons go, and what was opened for it folds again."""
        buttons, self.asking = self.asking, None
        if buttons is None:
            return
        self.grid.remove(buttons)
        discard(buttons)
        self.remove_css_class("asking")
        if self.opened_to_ask:
            self.opened_to_ask = False
            self.input_toggle.set_expanded(False)
        self.show_status()
        for fn in self.listeners:
            fn(self)

    def set_decision(self, decision):
        """'allowed' / 'denied' / 'always allowed' from a permission prompt; only a denial is said in the header (an
        allowed call runs, then is done: that says it)."""
        self.decision = decision
        self.refresh_header()

    def refresh_header(self):
        s = summarize_tool(self.name, self.input)
        extra = []
        if self.state == "background":
            extra.append("running in background")
        elif self.state == "interrupted":
            extra.append("interrupted")
        if self.decision == "denied":
            extra.append(self.decision)
        tail = "".join(f"  <span alpha='55%'>· {html.escape(e)}</span>" for e in extra)
        title, source = tool_display(self.name)
        self.title.set_markup(f"<b>{html.escape(title)}</b>")
        src = f"<span alpha='55%'>{html.escape(source)}</span>  " if source else ""
        shown = html.escape(s)
        if self.base in ("Grep", "Glob") and s and s == _first_line(self.input.get("pattern") or ""):
            shown = f"<tt>{shown}</tt>"  # (a pattern, as it's written: in the code's letters)
        markup = f"{src}{shown}{tail}"
        self.header.set_markup(markup)
        # (a selectable, ellipsized label given markup once it's shown draws it without the markup's look, GTK 4.24,
        # till its style next changes: the look given again as the label's own attributes)
        self.header.set_attributes(Pango.parse_markup(markup, -1, "\0")[1])

    def show_output(self):
        self.toggles.set_visible(True)
        if getattr(self, "view_btn", None) is not None and self.state != "error":
            return  # (a subagent's: View opens it in the side panel, in place of an Output to fold open; an Error stays)
        self.output_toggle.set_visible(True)

    def set_input(self, inp):
        self.input = inp if isinstance(inp, dict) else {}
        self.refresh_header()
        # a plain Read's only input is the path, which the header already shows
        redundant = self.name == "Read" and not (self.input.get("offset") or self.input.get("limit") or
                                                 self.input.get("pages"))
        self.input_toggle.set_visible(not redundant)
        # (no buttons, as an image's Read: nor their place, so the summary is a space after the name)
        self.toggles.set_visible(not redundant or self.output_toggle.get_visible())
        self.input_stale = True
        if self.input_toggle.get_expanded():
            self.build_input()
        for fn in self.listeners:
            fn(self)

    @property
    def media_box(self):
        """Images the tool returned, right under the header (an image Read is the image)."""
        if self._media_box is None:
            self._media_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, visible=False,
                                      margin_top=6, margin_bottom=2)
            self.grid.attach(self._media_box, 0, 3, 1, 1)
        return self._media_box

    def output_boxes(self):
        """(children, result_box): subagent messages land in the first when there's no side-panel view for them."""
        if self._output is None:
            out = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=6, margin_bottom=2)
            self._output = (Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4),
                            Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6))
            out.append(self._output[0])
            out.append(self._output[1])
            self.output_toggle.set_child(out)
        return self._output

    children = property(lambda self: self.output_boxes()[0])
    result_box = property(lambda self: self.output_boxes()[1])

    def build_input(self):
        if not self.input_stale:
            return
        self.input_stale = False
        if self.input_box is None:
            self.input_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin_top=6, margin_bottom=2)
            self.input_toggle.set_child(self.input_box)
        if (c := self.input_box.get_first_child()) is not None:
            self.input_box.remove(c)
            discard(c)
        self.input_box.append(tool_input_view(self.name, self.input))

    def build_output(self):
        pending, self.pending_output = self.pending_output, []
        read = self.input.get("file_path") if self.base == "Read" else None  # (a file's lines: its code, numbered)
        for shown, lang in pending:
            self.result_box.append(CodeBlock(shown, lang, path=read or "", numbered=bool(read)))

    def add_partial_json(self, chunk):
        self.partial_json += chunk
        try:
            self.set_input(json.loads(self.partial_json))
        except json.JSONDecodeError:
            pass

    def add_output_text(self, text, lang=""):
        # it scrolls inside its own pane now, so keep much more (a label this size is still cheap)
        shown = text if len(text) < 200_000 else text[:200_000] + f"\n… ({len(text)} chars)"
        self.pending_output.append((shown, lang))
        if self.output_toggle.get_expanded():
            self.build_output()
        self.show_output()

    def set_result(self, content, is_error):
        if self.asking is not None:  # (it ended without an answer, as a turn stopped: nothing to answer now)
            self.answered()
        texts, images = [], []
        for c in content if isinstance(content, list) else [content]:
            if isinstance(c, dict) and c.get("type") == "image" and c.get("source", {}).get("data"):
                images.append(base64.b64decode(c["source"]["data"]))
            elif isinstance(c, dict):
                texts.append(c.get("text", ""))
            elif c:
                texts.append(str(c))
        text = "\n".join(t for t in texts if t)
        if self.task is not None and (m := re.search(r"Output is being written to: (\S+)", text)):
            self.task.setdefault("output_file", m.group(1).rstrip("."))  # live output before it ends
        if text and self.base in ("Agent", "Task") and not text.startswith("Async agent launched"):
            if text.strip() != getattr(self, "report", None):  # (its task's ending may have shown it)
                self.report = text.strip()
                self.result_box.append(MarkdownView(text))  # a finished agent's answer is markdown
                self.show_output()
        elif text and not said_nothing(self.base, text, is_error):  # (it went through: no Output to open)
            self.add_output_text(text)
        for data in images:
            try:
                path = self.input.get("file_path") if self.base == "Read" else None
                self.media_box.append(image_widget(data, path=path))
            except GLib.Error:
                self.media_box.append(sel_label("[image could not be decoded]"))
        self.artifact = artifact_from_tool(self.input, text) if self.base == "Artifact" else None
        if self.artifact:
            ARTIFACT_TITLES[self.artifact["url"]] = self.artifact["title"]  # a publish is authoritative
            version = f"  <span alpha='55%'>v{self.artifact['version']}</span>" if self.artifact.get("version") else ""
            line = sel_label("Published " + link_markup(self.artifact["url"]) + version)
            line.add_css_class("artifact-line")
            self.media_box.append(line)
        if images or self.artifact or self._media_box is not None:
            self.media_box.set_visible(bool(images) or bool(self.artifact))
        # a backgrounded call's result only says it started: keep spinning until the task ends
        running_in_bg = self.task is not None and self.task.get("status", "running") == "running"
        self.set_state("error" if is_error else "background" if running_in_bg else "done")

    def bind_task(self, task):
        """The CLI turned this call into a background task (a command or a subagent)."""
        self.task = task
        if self.state == "done":  # the 'started' result came first
            self.set_state("background")

    def finish_task(self, task):
        """The background task ended: final status, summary and (for commands) its output."""
        self.task = task
        status = task.get("status")
        if task.get("summary") and task.get("task_type") != "local_bash" \
                and task["summary"].strip() != getattr(self, "report", None):
            self.report = task["summary"].strip()
            self.result_box.append(MarkdownView(task["summary"]))  # a subagent's report is markdown
            self.show_output()
        out = read_tail(task.get("output_file")) if task.get("task_type") == "local_bash" else ""
        if out:
            self.add_output_text(out, "output")
        elif task.get("task_type") == "local_bash" and task.get("summary"):
            self.add_output_text(task["summary"], status or "")
        self.set_state("done" if status == "completed" else "error")

    def has_media(self):
        return self._media_box is not None and self._media_box.get_visible()


def read_tail(path, limit=6000):
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - limit))
            data = f.read().decode("utf-8", "replace")
        return ("… " if size > limit else "") + data
    except (OSError, TypeError):
        return ""


class ToolGroup(Gtk.Grid):
    """Consecutive tool calls folded into one line, › 5 tool calls, 1 failed (a spinner after it while one runs);
    opened, the calls under it, a line each, their boxes' left edge where the chat's text starts."""

    def __init__(self):
        super().__init__()  # (no row gap: folded, nothing under its line)
        self.add_css_class("tool-group")
        self.state, self.status = "running", None
        self.row = Gtk.Box(spacing=8)
        self.attach(self.row, 0, 0, 1, 1)
        self.toggle = Disclosure(self.row, self, 1, "")
        self.toggle.text.remove_css_class("dim-label")
        self.items = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=4)
        self.toggle.set_child(self.items)

    def add_item(self, widget):
        self.items.append(widget)
        if isinstance(widget, ToolCard):
            widget.listeners.append(lambda *_: self.refresh())
        self.refresh()

    def cards(self):
        out, child = [], self.items.get_first_child()
        while child is not None:
            if isinstance(child, ToolCard):
                out.append(child)
            child = child.get_next_sibling()
        return out

    def refresh(self):
        cards = self.cards()
        failed = sum(c.state == "error" for c in cards)
        self.toggle.set_label(f"{len(cards)} tool calls" + (f", {failed} failed" if failed else ""))
        states = {c.state for c in cards if c.asking is None}  # (one waiting to be allowed isn't running)
        state = "running" if states & {"running", "background"} else "error" if "error" in states else \
            "interrupted" if "interrupted" in states and len(states) == 1 else "done"
        if state != self.state or self.status is None and state == "running":
            self.state = state
            if self.status is not None:
                self.row.remove(self.status)
            self.status = _status_icon(state) if state == "running" else None  # (a spinner says something; a tick doesn't)
            if self.status is not None:
                self.row.append(self.status)
        if any(c.has_media() or c.asking is not None for c in cards) and not self.toggle.get_expanded():
            self.toggle.set_expanded(True)  # images are the point, and a call asking you is waiting on you: shown


# ------------------------------------------------------- desktop launcher --
# Baude in the platform's list of apps, per user: Launcher, below.

ICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128">
  <rect x="8" y="8" width="112" height="112" rx="26" fill="#d97757"/>
  <path d="M30 38h68a8 8 0 0 1 8 8v36a8 8 0 0 1-8 8H58l-18 14v-14H30a8 8 0 0 1-8-8V46a8 8 0 0 1 8-8z"
        fill="#fff8f0"/>
  <path d="M40 54l12 10-12 10" stroke="#d97757" stroke-width="7" fill="none"
        stroke-linecap="round" stroke-linejoin="round"/>
  <path d="M60 76h22" stroke="#d97757" stroke-width="7" stroke-linecap="round"/>
</svg>
"""


def macos_icon_png(size=1024):
    """The logo (ICON_SVG) as a PNG the way macOS draws app icons: with the margin its own icons have (the tile 824
    of 1024)."""
    k = 112 * 1024 / 824  # (the tile is 112 of the SVG's 128)
    svg = ICON_SVG.replace('viewBox="0 0 128 128"', f'viewBox="{64 - k / 2} {64 - k / 2} {k} {k}"')
    loader = GdkPixbuf.PixbufLoader.new_with_type("svg")
    loader.set_size(size, size)
    loader.write(svg.encode())
    loader.close()
    return loader.get_pixbuf().save_to_bufferv("png", [], [])[1]


def macos_dock_icon():
    """On macOS, the Dock shows the logo (ICON_SVG) instead of Python's rocket: a Python process's Dock tile is
    Python.app's, and GTK's icon names don't reach it. Drawn at 1024 px (macos_icon_png), and handed to AppKit
    (-[NSApplication setApplicationIconImage:]) through the Objective-C runtime, as there's no PyObjC."""
    import ctypes
    png = macos_icon_png()
    data = objc_send(b"NSData", b"dataWithBytes:length:", png, len(png), types=(ctypes.c_char_p, ctypes.c_ulong))
    image = objc_send(objc_send(b"NSImage", b"alloc"), b"initWithData:", data, types=(ctypes.c_void_p,))
    objc_send(objc_send(b"NSApplication", b"sharedApplication"), b"setApplicationIconImage:", image,
              types=(ctypes.c_void_p,))


def launch_command(*args):
    """argv that starts baude: imported as a module, so Python uses its cached bytecode instead of
    compiling all of baude.py on every start (~150 ms; a script run directly is never cached)."""
    here = os.path.dirname(os.path.realpath(__file__))
    # (PYTHONDONTWRITEBYTECODE, inherited from some hosts, would leave the cache stale forever)
    boot = (f"import sys; sys.dont_write_bytecode = False; sys.path.insert(0, {here!r}); "
            "import baude; sys.exit(baude.main())")
    return [sys.executable, "-c", boot, *args]


class Launcher:
    """Baude in the platform's list of apps, for this user: LAUNCHER is this platform's. paths() are the files it
    writes, the first the one install() returns; where names the list."""
    where = ""

    def paths(self):
        raise NotImplementedError

    def write(self):
        raise NotImplementedError

    def refresh(self):
        """Tells the platform the files changed (optional: it notices anyway)."""

    def installed(self):
        return os.path.exists(self.paths()[0])

    def install(self):
        self.write()
        self.refresh()
        return self.paths()[0]

    def uninstall(self):
        removed = []
        for path in self.paths():
            if os.path.isdir(path):
                shutil.rmtree(path)
            elif os.path.exists(path):
                os.remove(path)
            else:
                continue
            removed.append(path)
        self.refresh()
        return removed


class FreedesktopLauncher(Launcher):
    """Linux: a .desktop entry in $XDG_DATA_HOME/applications and an icon in the user's hicolor theme, as the
    freedesktop specs have it."""
    where = "application menu"

    def paths(self):
        return [os.path.join(_data_home(), "applications", f"{APP_ID}.desktop"),
                os.path.join(_data_home(), "icons", "hicolor", "scalable", "apps", f"{APP_ID}.svg")]

    def write(self):
        entry = "\n".join([
            "[Desktop Entry]",
            "Type=Application",
            "Name=Baude",
            "GenericName=Claude Code client",
            "Comment=GTK front-end for the claude CLI",
            "Exec=" + " ".join(_exec_quote(a) for a in launch_command()),
            f"Icon={APP_ID}",
            "Terminal=false",
            "Categories=Development;GTK;",
            "Keywords=claude;ai;agent;chat;",
            "StartupNotify=true",
            f"StartupWMClass={APP_ID}",
            "",
        ])
        desktop, icon = self.paths()
        for path, data in ((icon, ICON_SVG), (desktop, entry)):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write(data)

    def refresh(self):
        # (menus watch the folder anyway; this refreshes the MIME cache)
        if shutil.which("update-desktop-database"):
            subprocess.run(["update-desktop-database", os.path.dirname(self.paths()[0])],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)


def _exec_quote(arg):
    """Quote an argument for a desktop-entry Exec= line (not shell quoting)."""
    arg = arg.replace("%", "%%")  # % starts a field code
    if re.fullmatch(r"[\w@%+=:,./-]+", arg):
        return arg
    return '"' + re.sub(r'(["`$\\])', r"\\\1", arg) + '"'


MACOS_LSREGISTER = "/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework" \
    "/Support/lsregister"


class MacLauncher(Launcher):
    """macOS: an app bundle, ~/Applications/Baude.app, which Spotlight and the Finder list. Its
    executable is a script that starts baude (launch_command) through the user's login shell, as an app opened
    from the Finder gets launchd's bare PATH, without Homebrew's tools. Its icon is an .icns of PNGs (the
    format's ic07 to ic10 entries, 128 to 1024 px)."""
    where = "Applications"

    def __init__(self, folder=None):
        self.folder = folder or os.path.expanduser("~/Applications")

    def paths(self):
        return [os.path.join(self.folder, "Baude.app")]

    @staticmethod
    def icns():
        entries = b"".join(kind + struct.pack(">I", 8 + len(png)) + png
                           for kind, png in ((k, macos_icon_png(n)) for k, n in
                                             ((b"ic07", 128), (b"ic08", 256), (b"ic09", 512), (b"ic10", 1024))))
        return b"icns" + struct.pack(">I", 8 + len(entries)) + entries

    def write(self):
        import plistlib
        import shlex
        contents = os.path.join(self.paths()[0], "Contents")
        os.makedirs(os.path.join(contents, "MacOS"), exist_ok=True)
        os.makedirs(os.path.join(contents, "Resources"), exist_ok=True)
        with open(os.path.join(contents, "Info.plist"), "wb") as f:
            plistlib.dump({
                "CFBundleName": "Baude",
                "CFBundleDisplayName": "Baude",
                "CFBundleIdentifier": APP_ID,
                "CFBundlePackageType": "APPL",
                "CFBundleExecutable": "Baude",
                "CFBundleIconFile": "Baude",
                "LSApplicationCategoryType": "public.app-category.developer-tools",
                "NSHighResolutionCapable": True,
            }, f)
        with open(os.path.join(contents, "Resources", "Baude.icns"), "wb") as f:
            f.write(self.icns())
        script = os.path.join(contents, "MacOS", "Baude")
        with open(script, "w") as f:
            f.write("#!/bin/sh\n"
                    'cd "$HOME"\n'  # (the Finder starts apps in /; Linux's launchers in your home)
                    'exec "${SHELL:-/bin/zsh}" -lc \'exec "$0" "$@"\' '
                    + " ".join(shlex.quote(a) for a in launch_command()) + ' "$@"\n')
        os.chmod(script, 0o755)

    def refresh(self):
        # (LaunchServices and Spotlight find the bundle in time, a minute or more; this lists it now, for ⌘Space too)
        path = self.paths()[0]
        if os.path.exists(MACOS_LSREGISTER):
            subprocess.run([MACOS_LSREGISTER, "-f" if os.path.exists(path) else "-u", path],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        if os.path.exists(path) and shutil.which("mdimport"):
            subprocess.run(["mdimport", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)


LAUNCHER = MacLauncher() if sys.platform == "darwin" else FreedesktopLauncher()


# ------------------------------------------------------------ attachments --
# Images and PDFs go to the model as content blocks; anything else is passed
# as a path so Claude can open it with its own tools.

API_IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
MAX_IMAGE_EDGE = 1568           # larger images are downscaled by the API anyway
MAX_IMAGE_BYTES = 3_750_000     # 5 MB once base64-encoded
MAX_PDF_BYTES = 32_000_000


def _pixbuf_from_bytes(data):
    loader = GdkPixbuf.PixbufLoader()
    loader.write(data)
    loader.close()
    return loader.get_pixbuf()


def _encode_pixbuf(pb):
    ok, png = pb.save_to_bufferv("png", [], [])
    if ok and len(png) <= MAX_IMAGE_BYTES:
        return "image/png", png
    if pb.get_has_alpha():  # JPEG has no alpha: flatten onto white
        flat = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, pb.get_width(), pb.get_height())
        flat.fill(0xFFFFFFFF)
        pb.composite(flat, 0, 0, pb.get_width(), pb.get_height(), 0, 0, 1, 1,
                     GdkPixbuf.InterpType.BILINEAR, 255)
        pb = flat
    for quality in ("90", "75", "60"):
        ok, jpg = pb.save_to_bufferv("jpeg", ["quality"], [quality])
        if ok and len(jpg) <= MAX_IMAGE_BYTES:
            return "image/jpeg", jpg
    raise ValueError("image too large even after compression")


def prepare_image(data, mime):
    """Return (media_type, bytes) the API accepts: a supported type, <= MAX_IMAGE_EDGE px, small enough."""
    pb = _pixbuf_from_bytes(data)
    w, h = pb.get_width(), pb.get_height()
    if mime in API_IMAGE_TYPES and max(w, h) <= MAX_IMAGE_EDGE and len(data) <= MAX_IMAGE_BYTES:
        return mime, data
    scale = min(1.0, MAX_IMAGE_EDGE / max(w, h))
    if scale < 1.0:
        pb = pb.scale_simple(max(1, round(w * scale)), max(1, round(h * scale)), GdkPixbuf.InterpType.HYPER)
    return _encode_pixbuf(pb)


class Attachment:
    """kind: 'image' | 'pdf' (sent as content blocks) or 'file' (sent as a path)."""

    def __init__(self, name, kind, media_type=None, data=None, path=None):
        self.name, self.kind, self.media_type, self.data, self.path = name, kind, media_type, data, path

    @classmethod
    def from_path(cls, path):
        path = os.path.abspath(path)
        name = os.path.basename(path)
        ctype, _ = Gio.content_type_guess(path, None)
        mime = Gio.content_type_get_mime_type(ctype) or ""
        try:
            if mime == "application/pdf" and os.path.getsize(path) <= MAX_PDF_BYTES:
                with open(path, "rb") as f:
                    return cls(name, "pdf", mime, f.read(), path)
            if mime.startswith("image/"):
                with open(path, "rb") as f:
                    media_type, data = prepare_image(f.read(), mime)
                return cls(name, "image", media_type, data, path)
        except (OSError, GLib.Error, ValueError):
            pass  # unreadable / undecodable: fall back to passing the path
        return cls(name, "file", mime, None, path)

    @classmethod
    def from_texture(cls, texture, name="pasted image.png"):
        media_type, data = prepare_image(texture.save_to_png_bytes().get_data(), "image/png")
        return cls(name, "image", media_type, data)

    def state(self):
        """As a restart keeps it (BaudeWindow.restart_state): a pasted image is only here."""
        return {"name": self.name, "kind": self.kind, "media_type": self.media_type, "path": self.path,
                "data": base64.b64encode(self.data).decode() if self.data else None}

    @classmethod
    def from_state(cls, state):
        return cls(state["name"], state["kind"], state["media_type"],
                   base64.b64decode(state["data"]) if state["data"] else None, state["path"])

    def block(self):
        if self.kind == "image":
            return {"type": "image", "source": {"type": "base64", "media_type": self.media_type,
                                                "data": base64.b64encode(self.data).decode()}}
        if self.kind == "pdf":
            return {"type": "document", "title": self.name,
                    "source": {"type": "base64", "media_type": "application/pdf",
                               "data": base64.b64encode(self.data).decode()}}
        return None

    def thumbnail(self, size, scale=2.0):
        """A square, centre-cropped texture, size logical pixels on a screen of this scale."""
        return thumbnail_texture(self.data, size, scale)

    def icon(self):
        return file_icon(self.name)


def clipboard_has(formats, gtype, mime_prefixes):
    """True if the clipboard offers `gtype` (or a subclass) locally, or a matching MIME type
    from another app. Other apps' clipboards only ever list MIME types."""
    if any(GObject.type_is_a(t, gtype) for t in formats.get_gtypes() or []):
        return True
    return any(m.startswith(p) for m in formats.get_mime_types() or [] for p in mime_prefixes)


def thumbnail_texture(data, size, scale):
    """A square, centre-cropped texture of an image's bytes: size logical pixels on a screen of this scale."""
    pb = _pixbuf_from_bytes(data)
    w, h = pb.get_width(), pb.get_height()
    side, px = min(w, h), max(1, round(size * scale))
    pb = pb.new_subpixbuf((w - side) // 2, (h - side) // 2, side, side).scale_simple(px, px,
                                                                                     GdkPixbuf.InterpType.BILINEAR)
    fmt = Gdk.MemoryFormat.R8G8B8A8 if pb.get_has_alpha() else Gdk.MemoryFormat.R8G8B8
    return Gdk.MemoryTexture.new(px, px, fmt, pb.read_pixel_bytes(), pb.get_rowstride())


def attachment_widget(att, size, on_remove=None):
    """A thumbnail (images) or icon + name, optionally with a remove button. A thumbnail is its size at once, and decoded
    off the main thread as it nears the view (widgets.FixedPicture): made here, a chat's history stopped for 9 ms an
    image as it was drawn, 300 ms a chunk of screenshots, mid-scroll."""
    box = Gtk.Box(spacing=4, valign=Gtk.Align.CENTER, tooltip_text=att.path or att.name)
    thumb = None
    if att.kind == "image" and widgets.image_dims(att.data):
        thumb = widgets.FixedPicture(size=(size, size), load=lambda scale, data=att.data:
                                     thumbnail_texture(data, size, scale))
    elif att.kind == "image":  # (a header image_dims can't read: decoded now, to know it's an image at all)
        try:
            thumb = Gtk.Image.new_from_paintable(att.thumbnail(size))
            thumb.set_pixel_size(size)
        except GLib.Error:  # (an image no loader here reads, say a HEIC sent from elsewhere: shown as a file is)
            thumb = None
    if thumb is not None:
        thumb.add_css_class("attach-thumb")
        make_image_interactive(thumb, att.data, att.path, att.name)
        box.append(thumb)
    else:
        box.append(Gtk.Image.new_from_gicon(att.icon()))
        name = Gtk.Label(label=att.name, ellipsize=Pango.EllipsizeMode.MIDDLE, max_width_chars=22)
        box.append(name)
    if on_remove:
        rm = Gtk.Button(icon_name="window-close-symbolic", tooltip_text="Remove", valign=Gtk.Align.CENTER)
        rm.add_css_class("flat")
        rm.add_css_class("circular")
        rm.connect("clicked", lambda *_: on_remove(att))
        box.append(rm)
    box.add_css_class("attach-chip")
    if thumb is not None and not on_remove:
        box.add_css_class("image")
    return box


# --------------------------------------------------------- slash completer --

LOCAL_COMMANDS = [  # handled by baude itself, never sent to claude
    {"name": "new", "description": "Start a fresh session", "argumentHint": "", "aliases": ["clear"]},
    {"name": "cd", "description": "Move the conversation to another folder", "argumentHint": "<path>"},
    {"name": "btw", "description": "Ask a side question: answered from this conversation, without adding to it",
     "argumentHint": "<question>"},
    {"name": "login", "description": "Sign in to your Anthropic account (claude auth login)",
     "argumentHint": "[--console | --sso | --email <address>]"},
    {"name": "logout", "description": "Sign out of your Anthropic account (claude auth logout)", "argumentHint": ""},
]


# ------------------------------------------------------------- /login --

_AUTH_ESCAPES = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-9;?]*[A-Za-z]")  # (a link's, a colour's)
NEEDS_LOGIN = re.compile(r"Please run /login|Invalid API key|OAuth token (?:has )?(?:expired|revoked)|"
                         r"[Nn]ot (?:logged|signed) in|authentication_error", re.I)


def needs_login(text):
    """A turn's error that signing in again would fix (claude's "Invalid API key · Please run /login")."""
    return bool(text) and bool(NEEDS_LOGIN.search(text))


def auth_status():
    """claude auth status --json: {loggedIn, email, orgName, subscriptionType, authMethod…}, or {} (it failed)."""
    try:
        out = subprocess.run([claude_exe(), "auth", "status", "--json"], capture_output=True, text=True, timeout=30,
                             stdin=subprocess.DEVNULL, env=claude_env()).stdout
        return json.loads(out)
    except (OSError, ValueError, subprocess.SubprocessError):
        return {}


def signed_in_as(status):
    """Who auth_status says is signed in, in a few words, or None."""
    if not status.get("loggedIn"):
        return None
    who = status.get("email") or status.get("authMethod") or "your account"
    extra = " · ".join(x for x in (status.get("orgName"), status.get("subscriptionType")) if x)
    return f"{who} ({extra})" if extra else who


class LoginCard(Gtk.Box):
    """/login in the chat: claude auth login run here (it needs no terminal: it writes lines, and reads a pasted code
    from its input). The sign-in page opens in the browser (its link here too, if it didn't); if the page shows a code
    instead of coming back by itself, it's pasted in the box; Cancel stops it. Once it's done, who's signed in, and the
    chat's claude started again on it (win.signed_in). args: claude auth login's (--console, --sso, --email …)."""

    def __init__(self, win, args=()):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.add_css_class("perm-card")
        self.win, self.args, self.proc, self.said = win, list(args), None, []
        self.title = sel_label("<b>Signing in to Claude…</b>")
        self.body = sel_label("<span alpha='60%'>Starting the sign-in…</span>")
        self.append(self.title)
        self.append(self.body)
        self.row = Gtk.Box(spacing=6)
        self.code = Gtk.Entry(hexpand=True, placeholder_text="If the page shows a code, paste it here")
        self.code.connect("activate", lambda *_: self.paste())
        self.send = Gtk.Button(label="Send")
        self.send.connect("clicked", lambda *_: self.paste())
        self.cancel = Gtk.Button(label="Cancel")
        self.cancel.connect("clicked", lambda *_: self.stop())
        for w in (self.code, self.send, self.cancel):
            self.row.append(w)
        self.append(self.row)
        self.again = Gtk.Button(label="Try again", halign=Gtk.Align.START, visible=False)
        self.again.add_css_class("suggested-action")
        self.again.connect("clicked", lambda *_: self.start())
        self.append(self.again)
        self.start()

    def start(self):
        self.title.set_markup("<b>Signing in to Claude…</b>")
        self.body.set_markup("<span alpha='60%'>Starting the sign-in…</span>")
        self.row.set_visible(True)
        self.again.set_visible(False)
        self.said = []
        try:
            self.proc = subprocess.Popen([claude_exe(), "auth", "login", *self.args], stdin=subprocess.PIPE,
                                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
                                         env=claude_env())
        except OSError as e:
            return self.done(None, f"couldn't start claude: {e}")
        threading.Thread(target=self._read, args=(self.proc,), daemon=True).start()

    def _read(self, proc):
        for line in proc.stdout:
            GLib.idle_add(self.heard, proc, line)
        GLib.idle_add(self.done, proc, None, proc.wait())

    def heard(self, proc, line):
        """A line from claude auth login: the sign-in page's address shown as a link; anything else kept, for if it
        fails."""
        if proc is not self.proc:
            return False
        # (its prompt has no line break: what it says next, "Login failed: …", comes on the prompt's line)
        line = _AUTH_ESCAPES.sub("", line).replace("Paste code here if prompted > ", "").strip()
        if m := re.search(r"https://\S+", line):
            url = m.group(0).rstrip(".,)")
            self.body.set_markup("A browser window should have opened to sign in. If it didn't, "
                                 f"<a href=\"{html.escape(url)}\">open the sign-in page</a>.")
        elif line and not line.startswith("Opening browser"):
            self.said.append(line)
        return False

    def paste(self):
        code = self.code.get_text().strip()
        if not code or self.proc is None or self.proc.poll() is not None:
            return
        try:
            self.proc.stdin.write(code + "\n")
            self.proc.stdin.flush()
        except OSError:
            return
        self.code.set_text("")
        self.body.set_markup("<span alpha='60%'>Checking the code…</span>")

    def stop(self):
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()

    def done(self, proc, problem=None, code=None):
        """It's finished: signed in (who as, from claude auth status), or why not, with Try again."""
        if proc is not None and proc is not self.proc:
            return False
        self.proc = None
        self.row.set_visible(False)
        if code == 0:
            self.title.set_markup("<b>Signing in to Claude…</b>")
            self.body.set_markup("<span alpha='60%'>Signed in; checking who as…</span>")

            def check():
                who = signed_in_as(auth_status())
                GLib.idle_add(self.signed_in, who)
            threading.Thread(target=check, daemon=True).start()
            return False
        why = problem or next((x for x in reversed(self.said) if x.lower().startswith(("login failed", "error"))),
                              None) or (self.said[-1] if self.said else None)
        self.title.set_markup("<b>Sign-in cancelled</b>" if code in (-15, 130) and not problem else
                              "<b>Sign-in didn't finish</b>")
        self.body.set_markup(f"<span alpha='60%'>{html.escape(why)}</span>" if why and code not in (-15,) else "")
        self.body.set_visible(bool(self.body.get_text()))
        self.again.set_visible(True)
        return False

    def signed_in(self, who):
        self.title.set_markup("<b>Signed in to Claude</b>")
        self.body.set_visible(True)
        self.body.set_markup(html.escape(f"as {who}") if who else "<span alpha='60%'>claude auth status didn't say "
                                                                   "who as</span>")
        self.win.signed_in()
        return False


class SlashCompleter:
    """/'s list, over the composer while the word the caret's at the end of starts with '/': the prompt's first word,
    or, anywhere in it, a / just typed at a word's start (after a space or a new line), as @ is in Record. Not a / in
    a path (src/app) or a link (https://…), nor in code. The commands, Baude's own and claude's, as they're typed,
    each with whose it is (Claude Code's own: its logo), its arguments and what it does. It looks and goes as Record's
    @ list does (widgets.PickList): ↑ ↓ Page Up and Page Down move, Enter or Tab or a click puts the command in where
    its /word was, Esc leaves it. (Only a prompt's first word is a command to Claude Code: one later on is words to
    Claude, which takes a skill's as asking for it.)"""

    WORD = re.compile(r"(?:^|(?<=\s))/(\S*)$")  # (a /word at the end of what's before the caret, at a word's start)

    def __init__(self, textview, anchor):
        self.view, self.anchor, self.commands, self.matches = textview, anchor, list(LOCAL_COMMANDS), []
        self.q, self.moved = None, False  # (what's being typed, and whether the arrows moved in its list)
        self.picks = widgets.PickList(anchor, self._bind, self.accept, position=Gtk.PositionType.TOP,
                                      about_ellipsis=Pango.EllipsizeMode.END)
        self.pop = self.picks.pop
        self.pop.set_halign(Gtk.Align.START)  # (its left side on the /'s: PickList.over)
        self.pop.set_offset(0, -10)  # a clear gap above the input box
        buf = textview.get_buffer()
        # (where a / was just typed: past the prompt's start, only that one starts the list, as only an @ just typed
        # does in Record; text pasted or put back that has a /word in it doesn't. A mark where it went in, so it goes
        # with the text, and how far into that the / is)
        self.typed, self.typed_in = buf.create_mark(None, buf.get_start_iter(), True), 0
        buf.connect("insert-text", self._inserting)
        buf.connect("changed", lambda *_: self.refresh())

    def _inserting(self, buf, at, text, _n):
        if "/" in text and len(text) <= 2:
            buf.move_mark(self.typed, at)
            self.typed_in = text.rfind("/")

    def set_commands(self, cmds):
        seen = {n for c in LOCAL_COMMANDS for n in [c["name"], *c.get("aliases", [])]}
        self.commands = list(LOCAL_COMMANDS) + sorted(
            (c for c in cmds if c.get("name") and c["name"] not in seen), key=lambda c: c["name"])
        self.refresh()

    def word(self):
        """(where its / is, what's typed after it) of the /word being typed, or None if we shouldn't complete: the
        caret's at its end (a space, a new line or the text's end next), and it's the prompt's first word or its / was
        just typed, not in code."""
        buf = self.view.get_buffer()
        text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True)
        cursor = buf.get_iter_at_mark(buf.get_insert()).get_offset()
        m = self.WORD.search(text, 0, cursor)
        if not m or text[cursor:cursor + 1].strip() or \
                m.start() and m.start() != buf.get_iter_at_mark(self.typed).get_offset() + self.typed_in or \
                self.in_code(text, m.start()):
            return None
        return m.start(), m.group(1).lower()

    def query(self):
        """The '/word' being typed, or None if we shouldn't complete."""
        word = self.word()
        return word and word[1]

    @staticmethod
    def in_code(text, offset):
        """Whether a place in the text is in a code block or a `code` span (a / there is a path's, a shell's)."""
        before = text[:offset]
        at = before.count("\n")
        return any(first <= at <= last for first, last, _closed in
                   widgets.fence_spans(text.split("\n"), tildes=False, typing=(at,))) or \
            before.rpartition("\n")[2].count("`") % 2 == 1

    def refresh(self):
        word = self.word()
        q = word and word[1]
        if q is None or "/" in q:  # (a path's, not a command)
            return self.hide()
        def rank(c):
            name, aliases = c["name"].lower(), [a.lower() for a in c.get("aliases") or []]
            if name.startswith(q):
                return 0
            if any(a.startswith(q) for a in aliases):
                return 1
            if any(q in n for n in [name, *aliases]):
                return 2
            return 3 if q and q in (c.get("description") or "").lower() else None
        i = self.picks.picked()
        kept = self.matches[i]["name"] if self.moved and q == self.q and i is not None and i < len(self.matches) \
            else None  # (the same, listed again, as claude's commands come in: what's picked stays picked)
        scored = [(r, c) for c in self.commands if (r := rank(c)) is not None]
        self.matches = [c for _, c in sorted(scored, key=lambda rc: rc[0])]
        at = next((j for j, c in enumerate(self.matches) if c["name"] == kept), None)
        self.q, self.moved = q, at is not None
        n = len(self.matches)
        # (as wide as @'s, or the input box, if that's narrower: its padding and line either side as well)
        wide, most = self.anchor.get_width() - 10, widgets.PickList.WIDTH
        self.picks.scroll.set_size_request(min(most, wide) if wide > 0 else most, -1)
        self.picks.show(n, at or 0, f"{n} found" if n else "Nothing found",
                        widgets.PickList.over(self.view, word[0], self.anchor))

    def _bind(self, i, icon, name, about):
        c = self.matches[i]
        icon.icon = "claude" if c.get("builtin") else "cli"
        name.set_text("/" + c["name"])
        desc = (c.get("description") or "").strip()
        also = ", ".join("/" + a for a in c.get("aliases") or [])
        about.set_text(" · ".join(x for x in (also and f"also {also}", c.get("argumentHint") or "",
                                              desc.split("\n")[0]) if x))
        about.set_tooltip_text(desc or None)

    def visible(self):
        return self.picks.visible()

    def hide(self):
        self.moved = False
        self.picks.hide()

    def accept(self):
        """The picked command in place of the /word, the caret after it and a space (the space hides us; one that was
        next already goes, so there's one)."""
        i, word = self.picks.picked(), self.word()
        if i is None or i >= len(self.matches) or word is None:
            return
        name, buf = f"/{self.matches[i]['name']} ", self.view.get_buffer()
        end = buf.get_iter_at_mark(buf.get_insert())
        if end.get_char() == " ":
            end.forward_char()
        buf.begin_user_action()
        buf.delete(buf.get_iter_at_offset(word[0]), end)
        buf.insert_at_cursor(name)
        buf.end_user_action()
        self.view.grab_focus()

    def handle_key(self, keyval, state=0):
        """Returns True if the key was consumed by the popover."""
        if not self.visible():
            return False
        steps = widgets.PickList.STEPS
        if state & Gdk.ModifierType.SHIFT_MASK and keyval in (*steps, Gdk.KEY_Left, Gdk.KEY_Right):
            self.hide()  # (Shift and an arrow: selecting the text, not picking in the list)
            return False
        if keyval in steps:
            if self.matches:
                self.picks.move(steps[keyval])
                self.moved = True
        elif keyval in (Gdk.KEY_Tab, Gdk.KEY_Return, Gdk.KEY_KP_Enter) and self.picks.picked() is not None:
            self.accept()
        elif keyval == Gdk.KEY_Escape:
            self.hide()
            return bool(self.matches)  # ("Nothing found": Esc does what it would have, as a turn's stop, too)
        else:
            return False
        return True


class ChoiceDropDown(Gtk.DropDown):
    """A DropDown over (value, label) pairs. on_pick(value) fires for user picks only,
    not for select()/set_choices() calls that mirror what the CLI reports."""

    def __init__(self, choices, tooltip, on_pick):
        super().__init__(tooltip_text=tooltip, css_classes=["choice"])
        # (its list centred under it: GTK's lines up their left sides, and a longer one runs on to the right)
        if (popup := next((w for w in walk(self) if isinstance(w, Gtk.Popover)), None)) is not None:
            popup.set_halign(Gtk.Align.CENTER)
        self.strings = Gtk.StringList()
        self.set_model(self.strings)
        self.on_pick, self.values, self.quiet = on_pick, [], False
        self.set_choices(choices)
        self.connect("notify::selected", self._changed)

    def _changed(self, *_):
        if not self.quiet and self.value() is not None:
            self.on_pick(self.value())

    def _quietly(self, fn):
        self.quiet = True
        try:
            fn()
        finally:
            self.quiet = False

    def set_choices(self, choices):
        """Replace the entries, keeping the current value selected if it is still there."""
        keep = self.value()
        def swap():
            self.values = [v for v, _ in choices]
            self.strings.splice(0, self.strings.get_n_items(), [lbl for _, lbl in choices])
            self.set_selected(self.values.index(keep) if keep in self.values else Gtk.INVALID_LIST_POSITION)
        self._quietly(swap)

    def select(self, value):
        """Show `value` as selected (appending it if unknown) without firing on_pick."""
        def pick():
            if value not in self.values:
                self.values.append(value)
                self.strings.append(value)
            self.set_selected(self.values.index(value))
        self._quietly(pick)

    def value(self):
        i = self.get_selected()
        return self.values[i] if i < len(self.values) else None

    def label(self):
        i = self.get_selected()
        return self.strings.get_string(i) if i < len(self.values) else ""


# --------------------------------------------------------------- sessions --
# (where Claude Code keeps transcripts, and reading one: baudelib.transcripts)


def collapse_copies(sessions, keep=()):
    """Drop stale copies of a conversation. When Claude Desktop continues a session it starts a
    new one and copies the whole history into it, leaving the old file behind: the sidebar showed
    "Baude GTK harness prototype" four times. An older session counts as a copy when a newer one
    in the same folder has the same title and holds at least 95% of its records (the last few
    differ) — or the newer one is itself 95% made of the older's records (Desktop sometimes copies
    only the history since the last compaction). Forks keep their own title ("... (fork)"), so they stay. Sessions in `keep` (the
    open one) are never dropped, nor is a chat Claude Desktop lists for another it lists (a side session of the same
    title, say: Desktop names a chat's copies itself, see apply_desktop_index). Survivors get "copies": how many were
    folded into them."""
    kept, newest_first = [], sorted(sessions, key=lambda i: i["mtime"], reverse=True)
    for info in newest_first:
        mine = record_ids(info)
        owner = None
        if info["id"] not in keep and sum(map(len, mine)) >= 5:
            for newer in kept:
                if info.get("desktop") and newer.get("desktop") and info["id"] != newer["id"]:
                    continue  # (two chats Claude Desktop lists are two chats: it says which are copies itself)
                theirs = record_ids(newer)
                if newer.get("title") == info.get("title") and newer.get("cwd") == info.get("cwd") \
                        and shared_records(mine, theirs) >= 0.95 * min(sum(map(len, mine)), sum(map(len, theirs))):
                    owner = newer
                    break
        if owner is None:
            kept.append({**info, "copies": info.get("copies", 0), "copy_paths": list(info.get("copy_paths", []))})
        else:
            owner["copies"] += 1
            owner["copy_paths"].append(info["path"])
            # the survivor stands for the whole conversation (where it forked from, when it began)
            fold_records(owner, info)
            owner["copied_from"] = (owner.get("copied_from") or frozenset()) | (info.get("copied_from") or frozenset())
            if info.get("born") and (not owner.get("born") or info["born"] < owner["born"]):
                owner["born"] = info["born"]
    return kept


def record_ids(info):
    """A session's record ids (record_key) as disjoint frozensets: its own, and the ones copies folded into it
    add (fold_records). The survivor of copies stands for the whole conversation without a union of them all:
    those were sets as big as the transcripts', made again on every sidebar refresh."""
    return [ids for ids in (info.get("uuids"), info.get("more_uuids")) if ids]


def shared_records(a, b):
    """How many records two record_ids have in common."""
    if len(a) == 1 == len(b):
        return len(a[0] & b[0])
    return sum(len(x & y) for x in a for y in b)


def fold_records(owner, copy):
    """Take a copy's records into the session that stands for it: those it doesn't have itself (the last few
    of a continued conversation's copy, or what a copy made since a compaction leaves out)."""
    own = owner.get("uuids") or frozenset()
    owner["more_uuids"] = frozenset().union(owner.get("more_uuids") or (), *(ids - own for ids in record_ids(copy)))


def desktop_parents(info):
    """The id of the session Claude Desktop shows a chat of its under, as a list ([] for none): the one that started it
    as a side session (spawned_from), or the one it was forked from (fork_of); apply_desktop_index sets one at most,
    as Desktop picks one (desktop_link). Where that one isn't listed, the chat is a top-level one."""
    return [i for i in (info.get("spawned_from"), info.get("fork_of")) if i]


_DESKTOP_LINKS = ("side_session", "hand_off", "suggested_task", "fork")


def desktop_link(entry):
    """(kind, local id) of the chat Claude Desktop nests one of its chats under, or (None, None), as its sidebar picks
    it: none once it's detached from it (lineageDetached); else its lineage; else the chat that started it as a side
    session (spawnedFrom, a side session's only); else the one it was forked from. kind is "fork" for a fork."""
    if entry.get("lineageDetached"):
        return None, None
    lineage = entry.get("lineage")
    if isinstance(lineage, dict) and isinstance(lineage.get("sessionId"), str) and lineage["sessionId"] \
            and lineage.get("kind") in _DESKTOP_LINKS and lineage.get("initiator") in ("claude", "user"):
        return lineage["kind"], lineage["sessionId"]
    spawned = entry.get("spawnedFrom") if isinstance(entry.get("spawnedFrom"), dict) else {}
    if spawned.get("sideSession") and isinstance(spawned.get("sessionId"), str):
        return "side_session", spawned["sessionId"]
    if isinstance(entry.get("forkedFromSessionId"), str):
        return "fork", entry["forkedFromSessionId"]
    return None, None


def desktop_used(entry):
    """When Claude Desktop says one of its chats was last used (what its sidebar's order and days go by), as a
    timestamp, or None: the last turn's start or end (lastTurnEdgeAt), else its last activity, else its creation."""
    ms = next((entry[k] for k in ("lastTurnEdgeAt", "lastActivityAt", "createdAt")
               if isinstance(entry.get(k), (int, float)) and entry[k] > 0), None)
    return ms / 1000 if ms is not None else None


def claude_desktop_dir():
    """The Claude desktop app's own folder: $XDG_CONFIG_HOME/Claude when that's set (Linux's way, and how the tests
    point it elsewhere), else ~/Library/Application Support/Claude on a Mac (where Electron keeps it), ~/.config/Claude
    elsewhere."""
    if os.environ.get("XDG_CONFIG_HOME"):
        return os.path.join(os.environ["XDG_CONFIG_HOME"], "Claude")
    return os.path.join(os.path.expanduser("~/Library/Application Support" if sys.platform == "darwin" else "~/.config"),
                        "Claude")


_FORK_SUFFIX = re.compile(r"^(.*\S)\s+\(([^()]+)\)$")


def fork_tree(items):
    """[(session, depth, is_last_sibling)] with forks under the session they came from.
    Nothing in a transcript says "forked from", but a fork starts as a copy of its parent's history:
    a session is a fork of the one it shares the most records with (10+) among those older than it.
    Older: the CLI's --fork-session copies keep the parent's sessionId; otherwise the earlier first
    timestamp; on a tie, Claude Desktop's naming ("<title> (fork)", or renamed "(Slug Colission)"). A Desktop
    chat is under the one Desktop says it came from (desktop_parents). A fork named "<parent's title> (...)" shows
    just the "..."."""
    def older(a, b):  # is a the elder of the pair?
        if a["id"] in (b.get("copied_from") or ()):
            return True
        if b["id"] in (a.get("copied_from") or ()):
            return False
        if a.get("born") and b.get("born") and a["born"] != b["born"]:
            return a["born"] < b["born"]
        m = _FORK_SUFFIX.match(b.get("title") or "")
        return bool(m) and m.group(1) == a.get("title")
    # (the open session has no id until its first message is sent: no one's parent, not even an unforked Desktop
    # chat's, whose fork_of is None too)
    parent, by_id, ids = {}, {i["id"]: i for i in items if i["id"]}, [record_ids(i) for i in items]
    for info, mine in zip(items, ids):
        if info.get("desktop"):  # Claude Desktop knows what each of its chats came from
            if (up := next((i for i in desktop_parents(info) if i in by_id), None)) is not None:
                parent[info["id"]] = by_id[up]
            continue
        best, shared = None, 9
        if sum(map(len, mine)) <= shared:
            continue  # (too few records to share 10 with anything)
        for cand, theirs in zip(items, ids):
            if cand is info or not older(cand, info):  # (before counting: half the pairs need no count)
                continue
            n = shared_records(mine, theirs)
            if n > shared:
                best, shared = cand, n
        if best is not None:
            parent[info["id"]] = best
    for info in items:  # a loop (shouldn't happen) is cut where it closes
        seen, cur = {info["id"]}, parent.get(info["id"])
        while cur is not None:
            if cur["id"] in seen:
                parent.pop(info["id"], None)
                break
            seen.add(cur["id"])
            cur = parent.get(cur["id"])
    for info in items:
        info.pop("fork_label", None)
        if (p := parent.get(info["id"])) is not None:
            m = _FORK_SUFFIX.match(info.get("title") or "")
            if m and m.group(1) == p.get("title"):
                info["fork_label"] = m.group(2)
    parent = {k: v["id"] for k, v in parent.items()}
    for info in items:
        info["fork_parent"] = parent.get(info["id"])
    children = {}
    for info in items:
        if info["id"] in parent:
            children.setdefault(parent[info["id"]], []).append(info)
    out = []

    def place(info, depth, last):
        out.append((info, depth, last))
        kids = children.get(info["id"], [])
        for i, kid in enumerate(kids):
            place(kid, depth + 1, i == len(kids) - 1)
    for info in items:
        if info["id"] not in parent:
            place(info, 0, True)
    return out


def desktop_index():
    """Claude Desktop's own list of its chats ({cli session id: entry}), from claude-code-sessions/*/*/local_*.json in
    its folder (claude_desktop_dir: on a Mac, in ~/Library/Application Support): title, archived, when it was last
    used, which earlier transcripts are copies of it (priorCliSessionIds) and what it came from. Empty without
    Desktop. Entries are cached by file mtime (they're small, but carry big tool lists)."""
    base = os.path.join(claude_desktop_dir(), "claude-code-sessions")
    with _info_lock:
        _load_info_cache()
    out, archived = {}, set()
    try:
        accounts = os.listdir(base)
    except OSError:
        return out
    for acct in accounts:
        for org in (os.listdir(os.path.join(base, acct)) if os.path.isdir(os.path.join(base, acct)) else []):
            d = os.path.join(base, acct, org)
            try:
                names = os.listdir(d)
            except OSError:
                continue
            for name in names:
                path = os.path.join(d, name)
                if name == "archived-sessions.idx":
                    entry = _desktop_file(path)
                    archived.update((entry or {}).get("archived") or [])
                elif name.startswith("local_") and name.endswith(".json"):
                    entry = _desktop_file(path)
                    if entry and entry.get("cliSessionId"):
                        out[entry["cliSessionId"]] = entry
    for e in out.values():
        e["_archived"] = bool(e.get("isArchived")) or e.get("sessionId") in archived
    return out


_desktop_cache = {}
# what's kept of each Desktop chat's file
_DESKTOP_KEYS = ("sessionId", "cliSessionId", "title", "worktreeName", "isArchived", "priorCliSessionIds",
                 "forkedFromSessionId", "spawnedFrom", "lineage", "lineageDetached", "lastTurnEdgeAt", "lastActivityAt",
                 "createdAt", "cwd", "archived")


def _desktop_file(path):
    try:
        mtime = os.path.getmtime(path)
        hit = _desktop_cache.get(path)
        if hit and hit[0] == mtime:
            return hit[1]
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return None
    entry = {k: raw[k] for k in _DESKTOP_KEYS if isinstance(raw, dict) and k in raw}
    _desktop_cache[path] = (mtime, entry)
    _info_dirty[0] = True  # (kept with the session index: these files are big, the entries small)
    return entry


def apply_desktop_index(sessions, index, keep=()):
    """Show Claude Desktop's chats the way Desktop does. A Desktop transcript its index doesn't list is
    gone from Desktop (deleted, or a stray from a test run with Desktop's environment): hidden. One
    it lists as an earlier copy of a chat folds into that chat. Listed chats take Desktop's title,
    archived state, when it was last used (desktop_used) and the one chat Desktop shows them under (desktop_link): the
    one that started them as a side session (spawned_from) or the one they were forked from (fork_of). Sessions from
    elsewhere (terminal, baude) are left alone."""
    if not index:
        return sessions
    prior = {p: cli for cli, e in index.items() for p in e.get("priorCliSessionIds") or ()}
    local = {e.get("sessionId"): cli for cli, e in index.items()}
    out, owners = [], {}
    for info in sessions:
        e = index.get(info["id"])
        if e is not None:
            kind, up = desktop_link(e)
            up = local.get(up) if up else None
            info = {**info, "desktop": True, "title": e.get("title") or e.get("worktreeName") or info.get("title"),
                    "desktop_archived": e["_archived"], "desktop_used": desktop_used(e),
                    "fork_of": up if kind == "fork" else None, "spawned_from": up if kind != "fork" else None}
            owners[info["id"]] = info
            out.append(info)
        elif info["id"] in prior and info["id"] not in keep:
            info["_copy_of"] = prior[info["id"]]
            out.append(info)
        elif info.get("source") == "Claude Desktop" and info["id"] not in keep:
            continue
        else:
            out.append(info)
    result = []
    for info in out:
        owner = owners.get(info.pop("_copy_of", None)) if "_copy_of" in info else None
        if owner is None:
            result.append(info)
            continue
        owner["copies"] = owner.get("copies", 0) + 1
        owner["copy_paths"] = owner.get("copy_paths", []) + [info["path"]]
        fold_records(owner, info)
        if info.get("born") and (not owner.get("born") or info["born"] < owner["born"]):
            owner["born"] = info["born"]
    return result


def list_sessions(progress=None):
    """All resumable sessions, newest first. Files are read newest first, and progress(so_far) is
    called (soon, then every 1.5 s) while unread ones remain: the first ever start reads hundreds of MB."""
    root = projects_dir()
    paths = []
    try:
        folders = os.listdir(root)
    except OSError:
        return []
    for folder in folders:
        try:
            names = os.listdir(os.path.join(root, folder))
        except OSError:
            continue
        for name in names:
            if name.endswith(".jsonl"):
                path = os.path.join(root, folder, name)
                try:
                    paths.append((os.path.getmtime(path), path))
                except OSError:
                    pass
    out, reported, shown = [], time.monotonic(), False
    for _mtime, path in sorted(paths, reverse=True):
        info = cached_session_info(path)
        if info:
            out.append(info)
        if progress is not None and time.monotonic() - reported > (0.4 if not shown else 1.5):
            reported, shown = time.monotonic(), True  # (often enough to see it come, not to slow it)
            progress(sorted(out, key=last_used, reverse=True))
    out.sort(key=last_used, reverse=True)
    return out


def last_used(info):
    """When a listed chat was last used: the last thing typed in it (scan_session's said), for the open one not saved
    yet now; for a Claude Desktop chat, when Desktop last saw it used (desktop_used, what Desktop's sidebar goes by),
    unless it was typed in since (carried on in a terminal, or here). What the sidebar's order and its days go by."""
    said = info.get("said") or info.get("mtime") or 0.0
    return max(said, info["desktop_used"]) if info.get("desktop_used") else said


_info_cache = {}  # path -> (inode, size, mtime, scan state, info); None -> loaded from disk yet
_info_lock = threading.Lock()


def cached_session_info(path):
    """read_session_info, remembered: an unchanged file isn't read again, a grown one (sessions are
    only ever appended to) is read from where it was left, and the memory survives restarts."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    with _info_lock:
        _load_info_cache()
        hit = _info_cache.get(path)
    if hit and hit[:3] == (st.st_ino, st.st_size, st.st_mtime):
        return hit[4]
    state = None
    if hit and hit[0] == st.st_ino and st.st_size > hit[1]:  # appended to: carry on from there
        state = hit[3]
    state, info = scan_session(path, state)
    with _info_lock:
        _info_cache[path] = (st.st_ino, st.st_size, st.st_mtime, state, info)
        _info_dirty[0] = True
    return info


def session_cost(path):
    """What claude had spent on a session when it last exited (its last cost-state), as cached_session_info says: from
    it while it has the transcript as it is, or has only what was appended since to read; else from the transcript's
    end back to that record (a chat opened before the session index has read it: read whole, a 160 MB one took 0.26 s
    of opening it). 0.0 for none."""
    try:
        st = os.stat(path)
    except OSError:
        return 0.0
    with _info_lock:
        _load_info_cache()
        hit = _info_cache.get(path)
    if hit and (hit[:3] == (st.st_ino, st.st_size, st.st_mtime) or hit[0] == st.st_ino and st.st_size > hit[1]):
        return (cached_session_info(path) or {}).get("cost") or 0.0
    sid = os.path.splitext(os.path.basename(path))[0]
    try:
        with open(path, "rb") as f:
            at, carry = st.st_size, b""
            while at > 0:
                start = max(0, at - (1 << 20))
                f.seek(start)
                data = f.read(at - start) + carry
                whole = data.find(b"\n") + 1 if start else 0  # (the line it begins in is read with the block before)
                carry, at = data[:whole], start
                hit = data.rfind(b'"cost-state"', whole)
                while hit >= 0:
                    begin, stop = data.rfind(b"\n", 0, hit) + 1, data.find(b"\n", hit)
                    if stop >= 0:  # (a whole line, as scan_session reads them: not one still being written)
                        tally = {**_scan_state(), "cost": None}
                        _scan_line(tally, str(memoryview(data)[begin:stop], "utf-8", "replace"), sid)
                        if tally["cost"] is not None:
                            return tally["cost"]
                    hit = data.rfind(b'"cost-state"', whole, begin)
    except OSError:
        pass
    return 0.0


_info_dirty = [False]
_INFO_CACHE_VERSION = 6  # (4: record ids as packed record_keys; 5: cost-state's cost; 6: when last typed in)


def _info_cache_path():
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return os.path.join(base, "baude", "sessions.json")


def _load_info_cache():
    """(caller holds _info_lock) Once per run: what was read last time, from disk."""
    if _info_cache.get(None) is not None:
        return
    _info_cache[None] = True  # (loaded marker)
    try:
        with open(_info_cache_path(), encoding="utf-8") as f:
            raw = json.load(f)
        if raw.get("version") == _INFO_CACHE_VERSION:  # (another version's files are read again: not the rest, the
            seen = {}                                   # worktrees seen above all, which can be gone since)
            for path, (ino, size, mtime, state, info) in raw["files"].items():
                state["uuids"], state["foreign"] = _unpack_keys(state["uuids"], seen), frozenset(state["foreign"].split())
                if info is not None:  # (its ids are the state's: stored once, and shared in memory too)
                    info["uuids"], info["copied_from"] = state["uuids"], state["foreign"]
                _info_cache[path] = (ino, size, mtime, state, info)
        if raw.get("desktop_keys") == list(_DESKTOP_KEYS):  # (entries kept with other keys are read again)
            for path, (mtime, entry) in raw.get("desktop", {}).items():
                _desktop_cache.setdefault(path, (mtime, entry))
        for path, seen in raw.get("worktrees", {}).items():  # (see main_folder)
            _WORKTREES_SEEN.setdefault(path, seen)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        pass


_info_saved = [0.0]


def save_info_cache(force=False):
    """Write the session index to disk if it changed (files that are gone are dropped). At most
    every 30 s unless forced (on exit): a live session changes it constantly, and encoding a few
    MB holds the interpreter lock, which the UI thread needs too."""
    if not force and time.monotonic() - _info_saved[0] < 30:
        return
    with _info_lock:
        if not _info_dirty[0]:
            return
        _load_info_cache()  # (a worktree learned before the index was read doesn't write over it)
        _info_dirty[0] = False
        _info_saved[0] = time.monotonic()
        files = {}
        for path, entry in _info_cache.items():
            if path is None or not os.path.exists(path):
                continue
            ino, size, mtime, state, info = entry
            files[path] = [ino, size, mtime,
                           {**state, "uuids": _pack_keys(state["uuids"]), "foreign": " ".join(state["foreign"])},
                           None if info is None else {k: v for k, v in info.items()
                                                      if k not in ("uuids", "copied_from")}]
        desktop = {p: list(v) for p, v in _desktop_cache.items() if os.path.exists(p)}
        worktrees = dict(_WORKTREES_SEEN)
    target = _info_cache_path()
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        data = json.dumps({"version": _INFO_CACHE_VERSION, "files": files, "desktop": desktop,
                           "desktop_keys": _DESKTOP_KEYS, "worktrees": worktrees},
                          separators=(",", ":"))  # (dumps: the C encoder; dump() to a file isn't)
        tmp = target + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(data)
        os.replace(tmp, target)
    except OSError:
        pass


# where Claude Desktop makes its "No folder" chats' folders: in its own folder (claude_desktop_dir), a Mac's and
# Linux's
SCRATCH_MARKS = tuple(os.sep + os.path.join(*parts, "Claude", "scratch-workspaces") + os.sep
                      for parts in ((".config",), ("Library", "Application Support")))


def is_scratch(path):
    """Is path in one of Claude Desktop's per-chat scratch folders (SCRATCH_MARKS)?"""
    return any(mark in path for mark in SCRATCH_MARKS)


def session_group(cwd):
    """(group key, label) for a session's folder; Claude Desktop's per-chat scratch folders
    are one group, like its "No folder" chats."""
    if not cwd:
        return "?", "Unknown folder"
    if is_scratch(cwd):
        return "scratch", "No folder (Claude Desktop)"
    return cwd, pretty_path(cwd)


def short_group_label(key, label):
    """A group's name as its header shows it: a folder's last part, the rest as they are."""
    if key in ("scratch", "?", "archived") or key.startswith("date:"):
        return label
    return os.path.basename(label.rstrip("/")) or label


def date_group(mtime, now):
    """(key, label) of a chat's day by its last activity, in local time, as Claude Desktop's sidebar has them: "Today",
    "Yesterday", each of the 5 days before ("Oct 20"), else "Older". Keys say how many days ago (what's collapsed stays
    as the days move on)."""
    import datetime
    day = datetime.date.fromtimestamp(mtime)
    ago = (datetime.date.fromtimestamp(now) - day).days
    if ago <= 0:  # (a clock that's behind: today)
        return "date:0", "Today"
    if ago == 1:
        return "date:1", "Yesterday"
    return (f"date:{ago}", f"{day:%b} {day.day}") if ago <= 6 else ("date:older", "Older")


def next_midnight(now):
    """The next local midnight after now (a timestamp), summer time and all."""
    import datetime
    day = datetime.date.fromtimestamp(now) + datetime.timedelta(days=1)
    return datetime.datetime.combine(day, datetime.time()).timestamp()


WORKTREES_MARK = os.sep + os.path.join(".claude", "worktrees") + os.sep  # where Claude Code and Desktop make theirs
_MAINS = {}           # folder -> main_folder's answer (this run)
_WORKTREES_SEEN = {}  # a worktree's folder -> [its main checkout, its top folder], kept with the session index


def main_folder(path):
    """(the folder a chat that ran in path belongs to, the top folder of the worktree it ran in, or None). A git
    worktree, a sibling made with `git worktree add` or the .claude/worktrees/<name> Claude Code and Claude Desktop
    make in a repository, isn't a folder of its own: its chats belong to the repository's main checkout (the parent of
    its common git folder, else `git worktree list`'s first; a bare repository's worktrees stay themselves), and a
    folder in a worktree to the same folder there. One that's gone is known from when it was last seen, or by its
    .claude/worktrees/ path; else it is its own folder, like a plain one. git runs once per folder, and only in one
    whose nearest .git is a file (a worktree's or a submodule's), so hundreds of sessions cost a few stats."""
    if not path or is_scratch(path):
        return path, None
    if (hit := _MAINS.get(path)) is not None:
        return hit
    found = (path, None)
    if os.path.isdir(path):
        up = path
        while not os.path.lexists(os.path.join(up, ".git")) and os.path.dirname(up) != up:
            up = os.path.dirname(up)
        if os.path.isfile(os.path.join(up, ".git")) and (co := git_checkout(path)) is not None:
            top, common = co
            main = os.path.dirname(common) if os.path.basename(common) == ".git" else None
            if main is None:  # (a separate git folder, or a bare repository: no main checkout there)
                first = (_git(path, "worktree", "list", "--porcelain") or "").split("\n\n")[0].splitlines()
                if first and first[0].startswith("worktree ") and "bare" not in first:
                    main = os.path.realpath(first[0][9:])
            if main and main != top:
                found = (os.path.normpath(os.path.join(main, os.path.relpath(os.path.realpath(path), top))), top)
                if _WORKTREES_SEEN.get(path) != [found[0], top]:
                    with _info_lock:
                        _WORKTREES_SEEN[path] = [found[0], top]
                        _info_dirty[0] = True
    else:
        with _info_lock:
            _load_info_cache()
        if (seen := _WORKTREES_SEEN.get(path)) is not None:
            found = tuple(seen)
        elif WORKTREES_MARK in path:
            repo, _, rest = path.partition(WORKTREES_MARK)
            name, _, sub = rest.partition(os.sep)
            found = (os.path.join(repo, sub) if sub else repo, repo + WORKTREES_MARK + name)
    _MAINS[path] = found
    return found


def place_sessions(sessions):
    """Each session's "folder", the one it's listed under (main_folder: a worktree's chat is its repository's), and
    "worktree", the worktree it ran in, if it did. Run off the main loop: git runs here, the sidebar only reads."""
    for info in sessions:
        info["folder"], info["worktree"] = main_folder(info.get("cwd"))
    return sessions


# (session_file, find_session, a transcript's items and HistoryIndex: baudelib.transcripts)


# ------------------------------------------------------------- the process --
# (claude_env, spent_since and ClaudeProc's workings: baudelib.claude)


def own_writes(proc, exiting=False):
    """A chat's claude left mid-turn wrote its transcript: every window takes those writes as its own (own_mtimes), so
    the chat opened after isn't one "active elsewhere". exiting: as it's stopped, with the cost it writes as it goes."""
    if getattr(proc, "path", None) is None:
        proc.path = (find_session(proc.key) or {}).get("path") if getattr(proc, "key", None) else None
    if proc.path is None:
        return
    try:
        mtime = os.stat(proc.path).st_mtime
    except OSError:
        return
    app = Gio.Application.get_default()
    for w in app.get_windows() if app is not None else []:
        if isinstance(w, BaudeWindow):
            w.own_mtimes[proc.path] = max(mtime, time.time() + 2) if exiting else mtime


def background_proc(session_id):
    """The claude a Baude window keeps at a turn for a chat left mid-turn (BaudeWindow.detach_proc), if any."""
    app = Gio.Application.get_default()
    for w in app.get_windows() if app is not None and session_id else []:
        if isinstance(w, BaudeWindow) and session_id in w.background:
            return w.background[session_id]
    return None


class ClaudeProc(claudelib.ClaudeProc):
    """Owns the claude subprocess (baudelib.claude's). Callbacks are delivered on the GTK main loop."""

    dispatch = staticmethod(GLib.idle_add)


class RemoteProc(baudelib.client.RemoteProc):
    """A session on a server on the network, talked to as a ClaudeProc's claude is (baudelib.client's), its callbacks
    on the GTK main loop too."""

    dispatch = staticmethod(GLib.idle_add)


def on_main(fn):
    """Run fn on the GTK main loop, once (whatever it returns)."""
    GLib.idle_add(lambda: fn() and False)


_NETWORK = []
_LOCAL_SERVER = {"wanted": None}
_HOST_SAID = {}  # (check_host_version's: the host process it's said is older, by its pid)  # (local_server's: what waits for the server on this machine it's started)


def network():
    """The hosts Baude knows, and their sessions (baudelib.client.Network): one for the app, started when it's first
    wanted. BAUDE_NETWORK=0: none looked for on the network or Tailscale; BAUDE_HOSTS=dest,…: these ssh destinations
    too."""
    if not _NETWORK:
        _NETWORK.append(baudelib.client.Network(
            on_main, discover=os.environ.get("BAUDE_NETWORK", "1") != "0",
            hosts=[a for a in os.environ.get("BAUDE_HOSTS", "").split(",") if a.strip()]))
    return _NETWORK[0]


def remote_info(s):
    """A session on the network (Network.sessions) as the sidebar and resume_session take a chat: its transcript's
    mirror here (baudelib.client.Remote.mirror) as its path."""
    r = s["server"]
    when = s.get("said") or s.get("created") or 0.0
    return {"id": s["id"], "path": r.mirror(s["id"]), "title": s.get("title"), "cwd": s.get("cwd"),
            "folder": s.get("cwd"), "worktree": None, "mtime": s.get("updated") or when, "said": when,
            "prompts": s.get("prompts") or 0, "source": f"{r.info.get('name') or r.host}, on the network", "copies": 0,
            # (this machine's host process, asleep: its sessions' claudes went with it, whatever was last listed)
            "remote": r, "remote_state": "stopped" if r.mine and r.state != "alive" else s.get("state"),
            "dead": r.state == "dead" and not r.mine,
            "group": s.get("group"), "model": s.get("model"), "effort": s.get("effort"), "mode": s.get("mode"),
            "archived": bool(s.get("archived"))}


def chat_groups(st=None):
    """{session id: the group a chat's been put in, "" for none}: settings.json's chat_groups (st: the settings, read
    already)."""
    found = (load_settings() if st is None else st).get("chat_groups")
    return {k: v for k, v in found.items() if isinstance(v, str)} if isinstance(found, dict) else {}


def made_groups(st=None):
    """The groups made (New Group…: SessionSidebar.make_group), empty or not: settings.json's group_names."""
    found = (load_settings() if st is None else st).get("group_names")
    return [g for g in found if isinstance(g, str) and g] if isinstance(found, list) else []


def chat_group(info, assigned=None):
    """A chat's group (⋮ → Groups), "" for none: Baude's own, whatever host the chat is on. The one it's been put in
    (assigned: chat_groups, read if not given); else, for a host's session (remote_info's, or Network.sessions' own),
    the one it was started in (baude_oneshot.py --group), unless that's only its host's own."""
    if (put := (chat_groups() if assigned is None else assigned).get(info.get("id"))) is not None:
        return put
    r = info.get("remote") or info.get("server")
    if r is None or not info.get("group") or info["group"] == r.info.get("group"):
        return ""
    return info["group"]


def remote_name(r):
    return r.info.get("name") or r.host


def local_host():
    """This machine's host process (its socket: Remote.mine), if it's there, which runs every chat of Baude's."""
    return next((r for r in network().alive() if r.mine), None)


def host_old():
    """Is This Mac's host process there but older than Baude (its protocol behind: no adopt, draft or ephemeral)?"""
    r = local_host()
    return r is not None and (r.info.get("version") or 1) < baudelib.wire.VERSION


def is_desktop(session_id):
    """Is a chat Claude Desktop's (in its records: desktop_index)? Those stay outside the host process."""
    return bool(session_id) and session_id in desktop_index()


HERE_HOST = "here"  # (⋮ → Hosts: this machine's key; another's is its server's)
THIS_MACHINE = "This Mac" if sys.platform == "darwin" else "This computer"


def host_key(r):
    """The host a server's sessions are listed under (⋮ → Hosts): this machine, for a server on it, as its own chats
    are; else the server's own."""
    return HERE_HOST if baudelib.client.is_here(r.info) else r.key


def remote_path(path, r):
    """A folder on a server as people say it there: its home folder is ~ (this machine's own: pretty_path)."""
    if baudelib.client.is_here(r.info):
        return pretty_path(path)
    home = (r.info.get("home") or "").rstrip("/")
    if home and path and (path == home or path.startswith(home + "/")):
        return "~" + path[len(home):]
    return path


# ------------------------------------------------------- sessions dialog --

class WorkingDots(Gtk.Label):
    """A turn running, at the start of the status bar: •, ••, •••, and again; otherwise it isn't there, nor the room it takes.
    All three are laid out, the rest see-through, so what's after it doesn't move with each dot. In a window you aren't
    looking at it holds still (Ticker)."""

    STEP_MS = 350

    def __init__(self):
        super().__init__(xalign=0, visible=False)
        self.working, self.shown, self.source = False, 0, None
        self.connect("map", WorkingDots._run)  # (plain functions: see Spinner)
        self.connect("unmap", WorkingDots._stop)
        self._show(0)

    def set_working(self, working):
        if working == self.working:
            return
        self.working = working
        self.set_visible(working)
        self.set_tooltip_text("Working" if working else None)
        self._show(1 if working else 0)
        self._stop()
        self._run()

    def _show(self, count):
        self.shown = count
        self.set_markup("•" * count + f'<span alpha="1">{"•" * (3 - count)}</span>')

    def _run(self):
        if self.working and self.source is None and self.get_mapped():
            self.source = GLib.timeout_add(self.STEP_MS, self._step)

    def _stop(self):
        if self.source is not None:
            GLib.source_remove(self.source)
            self.source = None

    def _step(self):
        if Ticker.seen(self):
            self._show(self.shown % 3 + 1)
        return True


class StatusDot(Gtk.Image):
    """A session's state: working (grey, pulsing), waiting (yellow: a prompt needs an answer),
    done (blue: the turn finished, waiting for your next message); inactive shows nothing."""

    TIPS = {"working": "Working", "waiting": "Waiting for your answer", "done": "Finished — waiting for input",
            "inactive": "Inactive"}

    def __init__(self, state="inactive"):
        ensure_icons()
        super().__init__(valign=Gtk.Align.CENTER)  # (the icons' size: Settings → Size)
        self.add_css_class("status-dot")
        self.state = None
        self.connect("map", StatusDot._mapped)  # (plain functions: see Spinner)
        self.connect("unmap", StatusDot._unmapped)
        self.set_state(state)

    def _mapped(self):
        if self.state == "working":
            Ticker.pulse(self)

    def _unmapped(self):
        Ticker.pulsing.discard(self)

    def set_state(self, state):
        if state == self.state:
            return
        if self.state:
            self.remove_css_class(self.state)
        self.state = state
        self.add_css_class(state)
        (Ticker.pulse if state == "working" else Ticker.still)(self)
        self.set_from_icon_name("baude-dot-symbolic")
        self.set_visible(state != "inactive")  # an inactive chat shows no dot at all
        self.set_tooltip_text(self.TIPS.get(state, state))


class FilterList(Gtk.ListBox):
    """One of the sidebar's ⋮ filters (Hosts, Groups), a list in its menu: what's ticked is listed, and everything is
    till some are picked. Picked as the Git page's branches are: a click, that one only; Shift or Ctrl (⌘) and a click,
    one more or one fewer (none left: everything again). changed(the keys ticked, or None: all of them) as it's
    picked; menu(row, x, y), x and y in the list's own, on a right-click on a row."""

    def __init__(self, changed, menu=None):
        super().__init__(selection_mode=Gtk.SelectionMode.MULTIPLE, activate_on_single_click=False,
                         css_classes=["filter-list"])
        self.changed, self.items, self.shown, self.filling, self.soon = changed, None, None, False, None
        self.connect("selected-rows-changed", lambda *_: self.picked())
        # (Shift and a click picks one more, or one fewer, as Ctrl (⌘) and a click does, and as on the Git page: not
        # the run from the last one picked, as GTK's list has it. Ahead of the list's own click, which never sees it)
        shift = Gtk.GestureClick(button=Gdk.BUTTON_PRIMARY, propagation_phase=Gtk.PropagationPhase.CAPTURE)
        shift.connect("pressed", self.shift_click)
        self.add_controller(shift)
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self.on_key)
        self.add_controller(keys)
        if menu is not None:
            right = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
            right.connect("pressed", lambda _g, _n, x, y: (row := self.get_row_at_y(int(y))) is not None
                          and menu(row, x, y))
            self.add_controller(right)

    def rows(self):
        row = self.get_first_child()
        while row is not None:
            if isinstance(row, Gtk.ListBoxRow):
                yield row
            row = row.get_next_sibling()

    def fill(self, items, shown):
        """items: [(key, label, note)], in order (note: a word, dim, after its name); shown: the keys ticked, or None
        for all of them. Built again only when the items change (a refill drops the keys' place in it)."""
        if self.soon is not None:  # (a click not said yet: said first, which fills this again if it changed it)
            GLib.source_remove(self.soon)
            if self._picked():
                return
        if items != self.items:
            self.filling = True
            for row in list(self.rows()):  # (rows only: a right-click's menu on it is a child of the list too)
                self.remove(row)
            for key, label, note in items:
                # (the tick where a menu's check items have theirs, its name where theirs is)
                box = Gtk.Box(spacing=2)
                tick = Gtk.Image(icon_name="object-select-symbolic", opacity=0)
                box.append(tick)
                box.append(Gtk.Label(label=label, xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END,
                                     max_width_chars=24))
                if note:
                    said = Gtk.Label(label=note)
                    said.add_css_class("dim-label")
                    box.append(said)
                row = Gtk.ListBoxRow(child=box)
                row.key, row.tick, row.label = key, tick, label
                self.append(row)
            self.items, self.shown, self.filling = items, (), False  # (ticked again, below)
        if shown != self.shown:
            self.choose(shown)

    def choose(self, shown):
        self.filling = True
        for row in self.rows():
            (self.select_row if shown is None or row.key in shown else self.unselect_row)(row)
        self.filling, self.shown = False, shown
        self.tick()

    def tick(self):
        for row in self.rows():
            row.tick.set_opacity(1 if row.is_selected() else 0)

    def shift_click(self, gesture, _n, _x, y):
        """A Shift-click on a row (Ctrl (⌘) not held too): it's picked if it wasn't, and put back if it was."""
        mods = gesture.get_current_event_state()
        if not mods & Gdk.ModifierType.SHIFT_MASK or mods & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.META_MASK):
            return
        row = self.get_row_at_y(int(y))
        if row is None:
            return
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        (self.unselect_row if row.is_selected() else self.select_row)(row)
        row.grab_focus()

    def picked(self):
        """The rows chosen changed (a click: one taken away and others put back, one at a time): said once, after."""
        self.tick()
        if not self.filling and self.soon is None:
            self.soon = GLib.idle_add(self._picked)

    def _picked(self):
        """(idle) Said, if it changed; True if so (and changed has filled the list again)."""
        self.soon = None
        rows = list(self.rows())
        keys = {r.key for r in rows if r.is_selected()}
        shown = None if not keys or len(keys) == len(rows) else keys
        news = shown != self.shown
        if not keys:  # (the last one taken away: everything, as at first)
            self.choose(None)
        self.shown = shown
        if news:
            self.changed(sorted(shown) if shown is not None else None)
        return news

    def on_key(self, _ctl, keyval, _code, state):
        """The arrows move among the rows without picking (GTK's list, picking several, picks the one moved to alone:
        a filter narrowed at every press); Space or Enter picks that one only, as a click does, and Ctrl (⌘) and
        Space adds or takes it away, as GTK's does. At either end: out of the list, to the menu's items."""
        moves = {Gdk.KEY_Up: -1, Gdk.KEY_KP_Up: -1, Gdk.KEY_Down: 1, Gdk.KEY_KP_Down: 1}
        if keyval not in moves or state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK):
            if keyval in (Gdk.KEY_space, Gdk.KEY_Return, Gdk.KEY_KP_Enter) and not state & (
                    Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.META_MASK):
                if (row := self.get_focus_child()) is not None:
                    self.unselect_all()
                    self.select_row(row)
                    return True
            return False
        rows = list(self.rows())
        at = rows.index(row) if (row := self.get_focus_child()) in rows else -1
        to = at + moves[keyval]
        if at < 0 or not 0 <= to < len(rows):
            return False
        rows[to].grab_focus()
        return True


class SessionSidebar(Gtk.Box):
    """The left pane, like Claude Desktop's: new session / open folder, search, and every
    session grouped by folder (this folder first), or by day (⋮ → Group by). Click one to switch to it, folder
    and all."""

    GROUP_LIMIT = 8  # sessions shown per other folder before "Show N more"
    DATE_LIMITS = {"date:0": None, "date:1": 10}  # by day: all of today's, 10 of yesterday's, else DATE_LIMIT
    DATE_LIMIT = 5

    def __init__(self, win):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, width_request=250)
        self.add_css_class("session-sidebar")
        self.win, self.sessions, self.expanded, self.loading = win, [], set(), False
        self.monitors, self.turn_cache, self.expiry = {}, {}, None  # see watch_rows
        self.clock, self.day, self.midnight = time.time, None, None  # see track_days
        top = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_start=8, margin_end=8,
                      margin_top=8, margin_bottom=4)
        self.new_btn = self._action("document-new-symbolic", "New session", "Ctrl+N", self.new_chat)
        self.folder_btn = self._action("folder-open-symbolic", "Open folder…", "Start a session in another folder",
                                       lambda: win.pick_folder())
        top.append(self.new_btn)
        top.append(self.folder_btn)
        find_row = Gtk.Box(spacing=2, margin_top=6)
        self.search = Gtk.SearchEntry(placeholder_text="Search sessions", hexpand=True)
        self.search.connect("search-changed", lambda *_: self.fill())
        find_row.append(self.search)
        view, by = Gio.Menu(), Gio.Menu()
        by.append("Folder", "sb.group::folder")
        by.append("Date", "sb.group::date")
        view.append_section("Group by", by)
        # (Hosts and Groups: which sessions are listed, each a list of its own in the menu (FilterList), filled by
        # fill_filters; a right-click on a host: New Session Here; on a group: Rename Group…, Delete Group)
        hosts, groups = Gio.Menu(), Gio.Menu()
        for menu, name in ((hosts, "hosts"), (groups, "groups")):
            item = Gio.MenuItem.new(None, None)
            item.set_attribute_value("custom", GLib.Variant.new_string(name))
            menu.append_item(item)
        hosts.append("Add Host…", "sb.add_server")  # (one not found by itself: ask_add_host)
        item = Gio.MenuItem.new("New Group…", None)  # (no chat moved to it: ask_new_group)
        item.set_action_and_target_value("sb.new_group", GLib.Variant.new_string(""))
        groups.append_item(item)
        every = Gio.MenuItem.new("Show All", "sb.show_all")  # (only while something's left out)
        every.set_attribute_value("hidden-when", GLib.Variant.new_string("action-disabled"))
        groups.append_item(every)
        view.append_section("Hosts", hosts)
        view.append_section("Groups", groups)
        pop = Gtk.PopoverMenu.new_from_model(view)
        self.host_list = FilterList(lambda keys: self.filtered(sidebar_hosts=keys), self.host_menu)
        self.group_list = FilterList(lambda keys: self.filtered(sidebar_groups=keys), self.group_menu)
        pop.add_child(self.host_list, "hosts")
        pop.add_child(self.group_list, "groups")
        self.view_btn = Gtk.MenuButton(icon_name="view-more-symbolic", popover=pop, valign=Gtk.Align.CENTER,
                                       tooltip_text="Group by, and which hosts and groups are listed")
        self.view_btn.add_css_class("flat")
        # (opened, or the window come forward: the network looked at again now, for a server started a moment ago)
        self.view_btn.connect("notify::active", lambda b, _p: b.get_active() and network().look())
        win.connect("notify::is-active", lambda w, _p: w.is_active() and network().look())
        find_row.append(self.view_btn)
        top.append(find_row)
        self.append(top)
        self.list = TreeList(selection_mode=Gtk.SelectionMode.SINGLE)
        self.list.add_css_class("navigation-sidebar")
        self.list.connect("row-activated", self.on_activate)
        self.scroller = Gtk.ScrolledWindow(child=self.list, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.append(self.scroller)
        # (at the foot, under a rule from the chats: Update Available, while a new version waits: show_update_bars)
        self.update_bar = widgets.UpdateBar(lambda: win.get_application().update_now(win))
        self.append(self.update_bar)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self.on_key)
        self.list.add_controller(keys)
        actions = Gio.SimpleActionGroup()
        for name, fn in (("rename", self.ask_rename), ("archive", self.archive), ("unarchive", self.unarchive),
                         ("trash", self.ask_trash), ("hide", self.hide_remote), ("stop", self.stop_remote)):
            act = Gio.SimpleAction.new(name, GLib.VariantType.new("s"))
            act.connect("activate", lambda _a, v, fn=fn: fn(self.find_info(v.get_string())))
            actions.add_action(act)
        group = Gio.SimpleAction.new_stateful("group", GLib.VariantType.new("s"),
                                              GLib.Variant.new_string("date" if self.by_date() else "folder"))
        group.connect("change-state", self.on_group_changed)
        actions.add_action(group)
        every = Gio.SimpleAction.new("show_all", None)
        every.connect("activate", lambda *_: self.filtered(sidebar_hosts=None, sidebar_groups=None))
        actions.add_action(every)
        here = Gio.SimpleAction.new("new_here", GLib.VariantType.new("s"))
        here.connect("activate", lambda _a, v: (pop.popdown(), self.win.new_session_here(v.get_string())))
        actions.add_action(here)
        add_server = Gio.SimpleAction.new("add_server", None)
        add_server.connect("activate", lambda *_: self.win.ask_add_host())
        take = Gio.SimpleAction.new("connect_host", GLib.VariantType.new("s"))
        take.connect("activate", lambda _a, v: (pop.popdown(), self.connect_host(v.get_string())))
        actions.add_action(take)
        command = Gio.SimpleAction.new("host_command", GLib.VariantType.new("s"))
        command.connect("activate", lambda _a, v: (pop.popdown(), self.win.ask_host_command(v.get_string())))
        actions.add_action(command)
        actions.add_action(add_server)
        # (groups: a chat right-clicked → Move to Group, its group ticked (the action's state: (its id, its group));
        # New Group… (a chat's id, to be moved to it; "": none); a group right-clicked in ⋮ → Rename Group…, Delete)
        move = Gio.SimpleAction.new_stateful("move_group", GLib.VariantType.new("(ss)"), GLib.Variant("(ss)", ("", "")))
        move.connect("activate", lambda _a, v: self.move_to_group([v.unpack()[0]], v.unpack()[1]))
        actions.add_action(move)
        for name, fn in (("new_group", lambda sid: self.ask_new_group(sid or None)),
                         ("rename_group", self.ask_rename_group), ("delete_group", self.delete_group_with_undo)):
            act = Gio.SimpleAction.new(name, GLib.VariantType.new("s"))
            act.connect("activate", lambda _a, v, fn=fn: (pop.popdown(), fn(v.get_string())))
            actions.add_action(act)
        self.insert_action_group("sb", actions)
        self.actions = actions
        self.remote_keys, self.remote_ids = {}, frozenset()  # (a server's folders: their servers; see groups)
        self.assigned = None  # (chat_groups, as groups last read them, for session_row)
        self.fill_filters()
        network().listen(self.on_network)
        win.connect("notify::is-active", lambda *_: self.by_date() and self.day != self.today() and self.fill())
        self.connect("unrealize", lambda *_: self.drop_midnight())  # (the window is closed: no more midnights)
        self.refresh()

    _action = staticmethod(sidebar_action)  # (widgets': Record's New page is one too)

    # ---- data
    def closed(self):
        """Its window is closed (BaudeWindow.let_go): no more lists, watches or timers for it."""
        return getattr(self.win, "let_go_done", False)

    def refresh(self):
        """Re-read the session list in the background (unchanged files come from the cache)."""
        if self.closed():
            return
        if self.loading:
            self.again = True
            return
        self.loading, self.again = True, False

        def work():
            keep = {self.win.session_id}
            index = desktop_index()
            shape = lambda found: place_sessions(  # noqa: E731
                collapse_copies(apply_desktop_index(found, index, keep), keep=keep))
            # (a slow first read shows the newest sessions as they come)
            sessions = shape(list_sessions(progress=lambda found: GLib.idle_add(partial, shape(found))))
            save_info_cache()
            GLib.idle_add(done, sessions)

        def partial(sessions):
            if self.loading and not self.closed():
                self.sessions = sessions
                self.fill()
                trace("sidebar shows the newest")
            return False

        def done(sessions):
            self.loading = False
            if self.closed():  # (closed while it read: what it found would watch files for nobody)
                return False
            self.sessions = sessions
            self.fill()
            trace("sidebar filled")
            if self.again:
                self.refresh()
            return False
        threading.Thread(target=work, daemon=True).start()

    def archived_ids(self):
        """Archived here, or in Claude Desktop (unless unarchived here)."""
        st = load_settings()
        unarchived = set(st.get("unarchived", []))
        return set(st.get("archived", [])) | {i["id"] for i in self.sessions
                                              if i.get("desktop_archived") and i["id"] not in unarchived}

    def by_date(self):
        """Grouped by day (⋮ → Group by → Date), not by folder."""
        return load_settings().get("sidebar_group") == "date"

    # ---- ⋮ → Hosts and Groups: which sessions are listed. A host is a machine: this one (THIS_MACHINE: Claude Code's
    # and Claude Desktop's chats, as ever, and those of a server on it), or another a server's on (baudelib.server),
    # its sessions kept as last seen and marked dead while it's gone. A group is Baude's own, a list of chats of its
    # own, whatever host they're on (chat_group); any chat can be moved to another (Move to Group, move_to_group).
    # Everything's listed till some are picked (FilterList)
    def known_hosts(self):
        """[(key, name, note)]: this machine, then each other host a server's on, by name (note: "gone", for one that
        is; "found", for one found on the network and not taken up: Connect, its right-click)."""
        out, seen = [(HERE_HOST, THIS_MACHINE, "")], {HERE_HOST}
        for r in sorted(network().remotes(), key=lambda r: remote_name(r).lower()):
            if (key := host_key(r)) not in seen:
                seen.add(key)
                out.append((key, remote_name(r), "found" if r.offered else "gone" if r.state == "dead" else ""))
        return out

    def known_groups(self):
        """[(name, label, note)]: each group, by name (those made, those chats are put in, and those hosts' sessions
        are in as they were started: chat_group), then No group ("")."""
        st = load_settings()
        assigned = chat_groups(st)
        names = set(made_groups(st)) | set(assigned.values()) | {chat_group(s, assigned) for s in network().sessions()}
        return [(g, g, "") for g in sorted(names - {""}, key=str.lower)] + [("", "No group", "")]

    def picked(self, name, known, was=None):
        """The hosts or groups picked (settings' sidebar_hosts, sidebar_groups) that are still there, or None: all of
        them (none of those picked there, or every one there picked). was: {a key a host had: the one it has now}."""
        shown = load_settings().get(name)
        if not isinstance(shown, list):
            return None
        keys = {key for key, *_ in known}
        shown = {(was or {}).get(k, k) for k in shown} & keys
        return shown if shown and shown != keys else None

    def host_filter(self):
        was = {old: host_key(r) for r in network().remotes() for old in getattr(r, "was", ())}
        return self.picked("sidebar_hosts", self.known_hosts(), was)

    def group_filter(self):
        return self.picked("sidebar_groups", self.known_groups())

    def shows(self, s, hosts, groups, assigned=None):
        """Is a server's session (Network.sessions') listed, with these hosts and groups picked (None: all)? assigned:
        chat_groups, read already."""
        return (hosts is None or host_key(s["server"]) in hosts) and \
            (groups is None or chat_group(s, assigned) in groups)

    def filtered(self, **picked):
        """Hosts or groups picked (sidebar_hosts=[keys], sidebar_groups=[names]; None: all of them, as is every one
        there): listed."""
        known = {"sidebar_hosts": self.known_hosts, "sidebar_groups": self.known_groups}
        save_settings(**{name: None if keys is None or set(keys) >= {k for k, *_ in known[name]()} else keys
                         for name, keys in picked.items()})
        self.fill_filters()
        self.fill()

    def fill_filters(self):
        """⋮ → Hosts and Groups, as the network has them now (what's picked that the network's lost: not shown, and
        not left out either, till it's back)."""
        if (old := load_settings().get("sidebar_network")) is not None:  # (⋮ → Group's one group, from before)
            save_settings(sidebar_network=None, sidebar_groups=[old] if old else None)
        self.host_list.fill(self.known_hosts(), self.host_filter())
        self.group_list.fill(self.known_groups(), self.group_filter())
        self.actions.lookup_action("show_all").set_enabled(  # (as it is after both: a fill can change what's picked)
            self.host_filter() is not None or self.group_filter() is not None)

    def host_menu(self, row, x, y):
        """Right-click a host (⋮ → Hosts): New Session Here (not on one that's gone); Host Command… for one reached
        over ssh."""
        r = next((r for r in network().remotes() if host_key(r) == row.key), None)
        menu = Gio.Menu()
        if r is not None and r.offered:  # (found on the network: reached only once it's taken up)
            item = Gio.MenuItem.new(f"Connect as {r.where.get('dest')}", None)
            item.set_action_and_target_value("sb.connect_host", GLib.Variant.new_string(row.key))
            menu.append_item(item)
        item = Gio.MenuItem.new("New Session Here", None)
        if row.key == HERE_HOST or r is not None and r.state == "alive":
            item.set_action_and_target_value("sb.new_here", GLib.Variant.new_string(row.key))
        else:
            item.set_action_and_target_value("sb.gone", None)  # (no such action: shown, but not to be picked)
        menu.append_item(item)
        if r is not None and r.where.get("via") == "ssh":  # (its host process's command there, if it isn't on PATH)
            item = Gio.MenuItem.new("Host Command…", None)
            item.set_action_and_target_value("sb.host_command", GLib.Variant.new_string(row.key))
            menu.append_item(item)
        self.host_menu_shown = self.menu_at(menu, self.host_list, x, y)

    def group_menu(self, row, x, y):
        """Right-click a group (⋮ → Groups): Rename Group…, Delete Group. No group has none."""
        if not row.key:
            return
        menu = Gio.Menu()
        for label, action in (("Rename Group…", "rename_group"), ("Delete Group", "delete_group")):
            item = Gio.MenuItem.new(label, None)
            item.set_action_and_target_value(f"sb.{action}", GLib.Variant.new_string(row.key))
            menu.append_item(item)
        self.group_menu_shown = self.menu_at(menu, self.group_list, x, y)

    def menu_at(self, menu, parent, x, y):
        """A right-click's menu, shown at x, y in parent (gone once it's closed)."""
        pop = Gtk.PopoverMenu(menu_model=menu, has_arrow=False)
        pop.set_parent(parent)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        pop.set_pointing_to(rect)
        pop.connect("closed", lambda p: GLib.idle_add(p.unparent))
        pop.popup()
        return pop

    def connect_host(self, key):
        """A host found on the network (offered), right-clicked → Connect: reached over ssh from now on, and kept."""
        if (r := next((r for r in network().remotes() if host_key(r) == key and r.offered), None)) is not None:
            network().accept(r)
            toast(self, f"Connecting to {r.host}")

    # ---- groups: chats moved to one, and groups made, renamed and deleted (settings' chat_groups and group_names).
    # Each saves, keeps what's picked in ⋮ → Groups (a group renamed: picked by its new name; deleted: not), lists it
    # so, and returns what undoes it: move_to_group's for restore_groups, the others' for restore_group_state
    def group_state(self, ids=None):
        """What restore_group_state puts back: {"chat_groups": {id: its entry, None if it has none} (ids', or every
        chat's with one), "group_names": the groups made, "sidebar_groups": the groups picked (None: all)}."""
        st = load_settings()
        assigned, picked = chat_groups(st), st.get("sidebar_groups")
        return {"chat_groups": {i: assigned.get(i) for i in (assigned if ids is None else ids)},
                "group_names": made_groups(st), "sidebar_groups": list(picked) if isinstance(picked, list) else None}

    def restore_group_state(self, state):
        """What group_state said put back, each of its keys there: its chats' entries (None: none, so a chat's group
        is the one chat_group gives it without), the groups made, the groups picked."""
        changes = {}
        if "chat_groups" in state:
            assigned = chat_groups()
            for i, group in state["chat_groups"].items():
                if group is None:
                    assigned.pop(i, None)
                else:
                    assigned[i] = group
            changes["chat_groups"] = assigned or None
        if "group_names" in state:
            changes["group_names"] = list(state["group_names"] or []) or None
        if "sidebar_groups" in state:
            changes["sidebar_groups"] = state["sidebar_groups"]
        save_settings(**changes)
        self.groups_changed()

    def groups_changed(self):
        self.fill_filters()
        self.fill()

    def move_to_group(self, ids, group):
        """Chats (their session ids) put in a group; "": in none, a host's session too, whatever it was started in.
        Returns {id: its chat_groups entry before, None if it had none}, for restore_groups."""
        ids = [ids] if isinstance(ids, str) else list(ids)
        assigned = chat_groups()
        before = {i: assigned.get(i) for i in ids}
        save_settings(chat_groups={**assigned, **dict.fromkeys(ids, group or "")})
        self.groups_changed()
        return before

    def restore_groups(self, before):
        """Chats' chat_groups entries put back as move_to_group found them ({id: entry, None: none})."""
        self.restore_group_state({"chat_groups": before})

    def group_members(self, group, assigned):
        """The ids of the chats in a group: those put in it, and hosts' sessions started in it and put in none."""
        return [i for i, g in assigned.items() if g == group] + \
            [s["id"] for s in network().sessions() if s["id"] not in assigned and chat_group(s, assigned) == group]

    def make_group(self, name):
        """A group made: listed in ⋮ → Groups and Move to Group, empty or not. Returns group_state() from before."""
        state = self.group_state(())
        if name and name not in state["group_names"]:
            save_settings(group_names=state["group_names"] + [name])
            self.groups_changed()
        return state

    def rename_group(self, old, new):
        """A group renamed: its chats (group_members) put in new, which is made in its place if it was made and picked
        in its place if it was; to a group there already, they join it. Returns group_state() from before."""
        if not old or not new or old == new:
            return self.group_state(())
        st = load_settings()
        assigned, picked = chat_groups(st), st.get("sidebar_groups")
        ids = self.group_members(old, assigned)
        state = self.group_state(ids)
        save_settings(chat_groups={**assigned, **dict.fromkeys(ids, new)} or None,
                      group_names=list(dict.fromkeys(new if g == old else g for g in made_groups(st))) or None,
                      sidebar_groups=sorted(set(picked) - {old} | {new})
                      if isinstance(picked, list) and old in picked else picked)
        self.groups_changed()
        return state

    def delete_group(self, name):
        """A group deleted: its chats (group_members) in none, and it neither made nor picked. Returns group_state()
        from before."""
        if not name:
            return self.group_state(())
        st = load_settings()
        assigned, picked = chat_groups(st), st.get("sidebar_groups")
        ids = self.group_members(name, assigned)
        state = self.group_state(ids)
        save_settings(chat_groups={**assigned, **dict.fromkeys(ids, "")} or None,
                      group_names=[g for g in made_groups(st) if g != name] or None,
                      sidebar_groups=sorted(set(picked) - {name}) or None if isinstance(picked, list) else picked)
        self.groups_changed()
        return state

    def delete_group_with_undo(self, name):
        """Delete Group (⋮ → Groups, a group right-clicked): deleted, said with Undo."""
        state = self.delete_group(name)
        if (root := self.get_root()) is not None and hasattr(root, "toasts"):
            said = Adw.Toast(title=f"Group “{GLib.markup_escape_text(name)}” deleted", button_label="Undo", timeout=5)
            said.connect("button-clicked", lambda *_: self.restore_group_state(state))
            root.toasts.add_toast(said)

    def ask_new_group(self, sid=None):
        """New Group…: a group made, and the chat sid moved to it, if given."""
        self.ask_group_name("New group", "Create", "",
                            lambda name: (self.make_group(name), sid and self.move_to_group([sid], name)))

    def ask_rename_group(self, name):
        """Rename Group… (⋮ → Groups, a group right-clicked)."""
        self.ask_group_name("Rename group", "Rename", name, lambda new: new != name and self.rename_group(name, new))

    def ask_group_name(self, heading, verb, text, then):
        """A group's name asked for, as ask_rename asks a chat's: then(the name, its spaces tidied), unless blank."""
        entry = Gtk.Entry(text=text, activates_default=True)
        dlg = Adw.AlertDialog(heading=heading, extra_child=entry, default_response="ok", close_response="cancel")
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("ok", verb)
        dlg.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        dlg.connect("response", lambda d, r: r == "ok" and (name := " ".join(entry.get_text().split())) and then(name))
        self.dialog = dlg
        dlg.present(self.win)
        entry.grab_focus()

    def on_network(self):
        """The network's servers or their sessions changed: ⋮'s hosts and groups, what's hidden coming back, and the
        list, when what it shows of them changed (which sessions are a server's, too: this machine's are listed by it,
        not as its own chats)."""
        if self.closed():
            return
        self.fill_filters()
        self.unhide_remote()
        hosts, groups, assigned = self.host_filter(), self.group_filter(), chat_groups()
        ids = frozenset(i for s in network().sessions() for i in (s["id"], s.get("cli_id")) if i)
        # (only what the rows show: a server says so every time a transcript grows, and a refill drops a menu open on a row)
        seen = sorted((s["id"], s.get("title"), s.get("state"), s["server"].state, s.get("cwd"), s.get("said"),
                       s.get("group"), bool(s.get("archived"))) for s in network().sessions()
                      if self.shows(s, hosts, groups, assigned))
        if ids != self.remote_ids or seen != getattr(self, "remote_seen", None):
            self.remote_seen = seen
            self.fill()

    def hidden_remote(self):
        """{session id: what it was as it was deleted} for the network's sessions deleted here (hide_remote)."""
        hidden = load_settings().get("hidden_remote")
        return hidden if isinstance(hidden, dict) else {}

    def unhide_remote(self):
        """A deleted session that shows up again is listed again: it's been used since, it's at a turn, or its server,
        gone as it was deleted, is back."""
        hidden = self.hidden_remote()
        if not hidden:
            return
        back = set()
        for s in network().sessions():
            if (h := hidden.get(s["id"])) is None:
                continue
            if (s.get("updated") or 0) > (h.get("updated") or 0) + 1 or s.get("state") in ("working", "waiting") or \
                    h.get("dead") and s["server"].state == "alive":
                back.add(s["id"])
        if back:
            save_settings(hidden_remote={k: v for k, v in hidden.items() if k not in back} or None)

    def on_group_changed(self, action, value):
        action.set_state(value)
        save_settings(sidebar_group=value.get_string())
        self.fill()

    def today(self):
        import datetime
        return datetime.date.fromtimestamp(self.clock())

    def groups(self):
        """[(key, label, folder, [(session, depth, is_last)])], each group's rows as fork_tree places them: by folder,
        this folder first, then by latest activity (a worktree's chats are in its repository's folder: see
        place_sessions; a Desktop chat is in the group of the one it came from; a session of a server on another host
        in that host's folder: "remote:<server>:<folder>"); or by day (date_group), newest first, where a session and
        those under it (its forks, a Desktop chat's side sessions) go together, by the latest activity of any of them,
        as Claude Desktop's sidebar has them: a fork used today brings the session it came from into today, with it.
        Only the hosts and groups picked in ⋮ (host_filter, group_filter; a chat's group: chat_group, the open one not
        saved yet's too); a server's sessions deleted here (hide_remote): not listed till they show up again."""
        q = self.search.get_text().strip().lower()
        here = session_group(self.win.cwd)[0]
        by_date, now = self.by_date(), self.clock()
        hosts, picked = self.host_filter(), self.group_filter()
        assigned = self.assigned = chat_groups()  # (session_row's too)
        marks = self.marks = self.meta_marks()  # (Meta's changes, listed where they were till they're kept: session_row)
        mine = hosts is None or HERE_HOST in hosts  # (this machine's own chats)
        groups, listed, gone = {}, [], []
        archived, hidden = self.archived_ids(), self.hidden_remote()
        # (a server's sessions aren't this machine's own chats, though one on this machine keeps their transcripts here)
        remote = self.remote_ids = frozenset(i for s in network().sessions() for i in (s["id"], s.get("cli_id")) if i)
        ghosts = [g for g in self.meta_ghosts() if not any(i["id"] == g["id"] for i in self.sessions)]
        for info in sorted(self.sessions + ghosts if mine else [], key=last_used, reverse=True):  # (the last typed in
            if info["id"] in remote or picked is not None and chat_group(info, assigned) not in picked and \
                    info["id"] not in marks:  # first)
                continue
            folder = info.get("folder", info.get("cwd"))
            key, label = session_group(folder)
            tree = os.path.basename(info.get("worktree") or "")
            if q and q not in f"{info.get('title') or ''} {label} {tree}".lower():
                continue
            if info["id"] in archived and info["id"] not in marks:
                gone.append(info)
                continue
            listed.append((info, key, label, folder))
        # a chat Desktop started from another (a side session, a fork) is in that one's group, under it, whatever its
        # own folder
        by_id = {info["id"]: (info, key, label, folder) for info, key, label, folder in listed}
        for info, key, label, folder in listed:
            top, seen = info["id"], set()
            while top not in seen and (up := next((i for i in desktop_parents(by_id[top][0]) if i in by_id), None)):
                seen.add(top)
                top = up
            _info, key, label, folder = by_id[info["id"] if top in seen else top]  # (a loop: where it is)
            groups.setdefault(key, [label, folder if key != "scratch" else None, []])[2].append(info)
        # a server's sessions: one on this machine's in its folder's group, with this machine's chats there; another
        # host's in a group of its own for each folder there, its name with it
        self.remote_keys = {}
        for info in sorted((remote_info(s) for s in network().sessions()  # (not a new chat not written in yet: a
                            if (self.shows(s, hosts, picked, assigned) or s["id"] in marks) and not s.get("draft")),
                           # (a draft, the open one's)
                           key=last_used, reverse=True):
            r = info["remote"]
            if q and q not in " ".join([info.get("title") or "", info.get("cwd") or "", remote_name(r)]).lower():
                continue
            if (info.get("archived") or info["id"] in archived) and info["id"] not in marks:  # (archived on its host;
                gone.append(info)  # or here, before hosts did)
                continue
            if info["id"] in hidden:
                continue
            folder = info.get("cwd") or "?"
            if host_key(r) == HERE_HOST:
                key, label = session_group(folder)
            else:
                key, label = f"remote:{r.key}:{folder}", f"{remote_path(folder, r)} on {remote_name(r)}"
                self.remote_keys[key] = r
            groups.setdefault(key, [label, folder, []])[2].append(info)
        # the open session may not be saved yet: shown once it's something (typed into, sent), never while it's blank
        sid = self.win.session_id
        if mine and not q and self.win.chat_begun() and (self.win.remote is None or self.win.remote.mine) and \
                (picked is None or chat_group({"id": sid}, assigned) in picked) and \
                not any(i["id"] == sid for g in groups.values() for i in g[2]):
            key, label = session_group(self.win.cwd)
            groups.setdefault(key, [label, self.win.cwd, []])[2].insert(0, {
                "id": sid, "title": getattr(self.win, "session_title", None) or "New session",
                "cwd": self.win.run_cwd, "folder": self.win.cwd, "worktree": main_folder(self.win.run_cwd)[1],
                "mtime": now, "prompts": 0, "unsaved": True})
        # (forks are found within a folder, by day too: a fork is in its session's folder. A folder with a server's
        # sessions and this machine's chats in it has them in one order: the open, unsaved one first, then the newest)
        order_of = lambda items: sorted(items, key=lambda i: (not i.get("unsaved"), -last_used(i)))  # noqa: E731
        groups = {k: [label, folder, fork_tree(order_of(items))] for k, (label, folder, items) in groups.items()}
        if by_date:
            trees = [[]]
            for _label, _folder, rows in groups.values():
                for row in rows:
                    if not row[1] and trees[-1]:
                        trees.append([])
                    trees[-1].append(row)
            # (the open, unsaved one first, then the newest; a sort keeps sessions' order where it's the same. Nothing
            # listed, a search that finds nothing say, leaves the one empty tree, which has no first session)
            newest = lambda rows: max(last_used(info) for info, _depth, _last in rows)  # noqa: E731
            trees = sorted(filter(None, trees), key=lambda t: (not t[0][0].get("unsaved"), -newest(t)))
            groups = {}
            for rows in trees:
                key, label = date_group(newest(rows), now)
                groups.setdefault(key, [label, None, []])[2].extend(rows)
        # (days don't overlap: by day the latest activity of any session in them puts them in order, by folder a
        # top-level session's)
        order = sorted(groups.items(), key=lambda kv: (kv[0] != here, -max(
            last_used(i) for i, depth, _ in kv[1][2] if by_date or not depth)))
        out = [(k, label, folder, rows) for k, (label, folder, rows) in order]
        if gone:
            out.append(("archived", "Archived", None, fork_tree(gone)))
        return out

    # ---- view
    def collapsed(self):
        c = load_settings().get("collapsed_folders")
        return set(c) if c is not None else {"archived"}

    def toggle_folder(self, key):
        c = self.collapsed()
        c.symmetric_difference_update({key})
        save_settings(collapsed_folders=sorted(c))
        self.fill()

    def limit(self, key):
        """How many of a group's top-level sessions (with their forks) show before "Show N more": all of this
        folder's, 8 of another's; by day, all of today's, 10 of yesterday's and 5 of an earlier day's."""
        if key.startswith("date:"):
            return self.DATE_LIMITS.get(key, self.DATE_LIMIT)
        return None if key == session_group(self.win.cwd)[0] else self.GROUP_LIMIT

    def track_days(self):
        """(by day) Regroup at the next local midnight. Past one (a machine that slept through it wakes to a late
        timer), the days move on: fill regroups, and so does the window coming to the front or going; what was
        expanded closes, and the timer is set again."""
        today = self.today()
        if today != self.day:
            if self.day is not None:
                self.expanded = {k for k in self.expanded if not k.startswith("date:")}
            self.drop_midnight()
            self.day = today
        if self.midnight is None:
            now = self.clock()
            self.midnight = GLib.timeout_add_seconds(int(next_midnight(now) - now) + 1, self.at_midnight)

    def drop_midnight(self):
        if self.midnight is not None:
            GLib.source_remove(self.midnight)
            self.midnight = None

    def at_midnight(self):
        self.midnight = None
        if self.by_date():
            self.fill()  # (which sets the next; one that fired a little early sets a short one)
        return False

    def fill(self):
        if self.closed():
            return
        # rebuilding the list loses the scroll position (and focus scrolls it): put it back
        adj = self.scroller.get_vadjustment()
        keep = adj.get_value()
        while (row := self.list.get_row_at_index(0)) is not None:
            self.list.remove(row)
            discard(row)
        current = None
        searching = bool(self.search.get_text().strip())
        collapsed = self.collapsed()
        by_date = self.by_date()
        if by_date:
            self.track_days()
        for key, label, folder, rows in self.groups():
            shut = key in collapsed and not searching
            self.list.append(self.header_row(key, label, folder, key == session_group(self.win.cwd)[0], shut))
            if shut:
                continue
            limit = None if searching or key in self.expanded else self.limit(key)
            # (the limit counts top-level sessions: their forks come with them)
            tops = [n for n, (_info, depth, _last) in enumerate(rows) if not depth]
            shown = rows if limit is None or len(tops) <= limit else rows[:tops[limit]]
            placed = {}
            for info, depth, last in shown:
                row = self.session_row(info, depth, last)
                row.tree_parent = placed.get(info.get("fork_parent"))
                if info["id"]:  # (not the open session before its first message: a root's fork_parent is None too)
                    placed[info["id"]] = row
                self.list.append(row)
                if info["id"] and info["id"] == self.win.session_id or info.get("unsaved"):
                    current = row
            if len(shown) < len(rows):
                more = Gtk.ListBoxRow(child=Gtk.Label(label=f"Show {len(rows) - len(shown)} more", xalign=0,
                                                      margin_start=12), activatable=True)
                more.get_child().add_css_class("dim-label")
                more.more_key = key
                self.list.append(more)
        if current is not None:
            self.list.select_row(current)
        self.watch_rows()

        def restore(*_):
            adj.set_value(keep)
            return False
        restore()
        GLib.idle_add(restore)  # again once the new rows are laid out

    def header_row(self, key, label, folder, is_here, collapsed=False):
        # (widgets.group_header: Record's pages and notebooks have the same; the arrow just right of the name, shown
        # when the pointer is near, or while collapsed)
        plus = ("list-add-symbolic", f"New session in {label}",
                lambda: self.win.switch_to(lambda: self.win.start_in_folder(folder))) \
            if folder and os.path.isdir(folder) else None
        if plus and not self.mine_listed():  # (a chat here wouldn't be listed: through This Mac's host, which is)
            plus = ("list-add-symbolic", f"New session in {label}, through {THIS_MACHINE}'s server",
                    lambda: self.win.ask_remote_start(folder=folder, here=True))
        if (r := self.remote_keys.get(key)) is not None:  # (a folder on a server: a session there, on it, if it's there)
            plus = ("list-add-symbolic", f"New session in {label}, on {remote_name(r)}",
                    lambda: self.win.ask_remote_start(server=r, folder=folder)) if r.state == "alive" else None
        box = widgets.group_header(short_group_label(key, label), collapsed, lambda: self.toggle_folder(key), plus,
                                   tooltip=label, current=is_here)
        box.set_margin_top(6)
        box.set_margin_start(4)
        row = Gtk.ListBoxRow(child=box, activatable=False, selectable=False)
        row.folder, row.key = folder, key
        return row

    def meta_marks(self):
        """{session id: (how, note, action)}: the chats Meta's changed and that aren't kept so yet, and those it's
        highlighted (BaudeMeta.marks)."""
        panel = getattr(self.win, "panel", None)
        return panel.meta.marks("chat") if panel is not None else {}

    def meta_ghosts(self):
        """The chats Meta's deleted and that aren't kept so: listed still, struck through (BaudeMeta.ghosts)."""
        panel = getattr(self.win, "panel", None)
        return panel.meta.ghosts() if panel is not None else []

    def session_row(self, info, depth=0, last=True):
        """Title ··· [dot] — one line; the dot only when something's happening (working, waiting for an answer,
        finished and waiting for input). Forks sit under their session like files in a tree. One Meta's changed:
        marked, with Keep and Undo (meta.mark_row)."""
        box = Gtk.Box(spacing=6, margin_start=6 + depth * TreeList.INDENT, margin_end=4)
        is_open = info.get("unsaved") or info["id"] == self.win.session_id
        dot = StatusDot(self.win.session_state() if is_open else self.file_state(info))
        label = info.get("fork_label") if depth else None
        # (a title too long for the row fades out where it's cut: widgets.FadeLabel)
        title = widgets.FadeLabel(label=label or info.get("title") or "(untitled)", xalign=0, hexpand=True)
        if depth:
            title.add_css_class("fork-title")
        box.append(title)
        r = info.get("remote")
        if info.get("dead"):  # (its server's gone from the network: listed as it was last seen)
            title.add_css_class("dim-label")
            tag = Gtk.Label(label="dead", valign=Gtk.Align.CENTER)
            tag.add_css_class("row-tag")
            box.append(tag)
        box.append(dot)
        tip = [info.get("title") or "", info.get("cwd") or ""]
        if r is not None:
            gone = f", gone from the network since {time_ago(r.seen)}" if info.get("dead") and r.seen else ""
            tip.append(f"On {remote_name(r)} ({r.host}){gone}")
        if group := chat_group(info, self.assigned):
            tip.append(f"Group {group}")
        if tree := info.get("worktree"):
            tip.append(f"Worktree {os.path.basename(tree)} of {pretty_path(info.get('folder') or '')}")
        if info.get("source") and r is None:
            tip.append(info["source"])
        if info.get("copies"):
            tip.append(f"{info['copies']} earlier cop{'y' if info['copies'] == 1 else 'ies'} of this conversation hidden")
        row = Gtk.ListBoxRow(child=box, tooltip_text="\n".join(t for t in tip if t))
        row.info, row.dot, row.depth, row.tree_anchor = info, dot, depth, title
        if (mark := getattr(self, "marks", {}).get(info["id"])) is not None:  # (Meta's change: meta.mark_row)
            meta.mark_row(row, ("chat", info["id"]), mark, self.win.panel.meta, title)
        if not info.get("unsaved") and not info.get("ghost"):
            click = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
            click.connect("pressed", lambda g, n, x, y: self.show_menu(row, x, y))
            row.add_controller(click)
        return row

    # ---- right-click menu / F2: rename, archive, move to trash
    def find_info(self, sid):
        """(a server on this machine keeps its sessions' transcripts here too: what the network says of one comes first)"""
        return next((remote_info(s) for s in network().sessions() if s["id"] == sid), None) or \
            next((i for i in self.sessions if i["id"] == sid), None)

    def show_menu(self, row, x, y):
        info, sid = row.info, GLib.Variant.new_string(row.info["id"])
        menu = Gio.Menu()
        items = [("Rename…", "rename"),
                 ("Unarchive" if info["id"] in self.archived_ids() else "Archive",
                  "unarchive" if info["id"] in self.archived_ids() else "archive"),
                 ("Move to Trash…", "trash")]
        if info.get("remote") is not None:  # (on a host: renamed, archived, stopped or trashed there while it's there;
            # while it's gone, only not listed here till it shows up again)
            active = not info.get("dead") and info.get("remote_state") in ("working", "waiting")
            archived = bool(info.get("archived")) or info["id"] in self.archived_ids()  # (here too: from before)
            items = [("Delete", "hide")] if info.get("dead") or info["remote"].state != "alive" else \
                [("Rename…", "rename")] + ([("Unarchive", "unarchive")] if archived else [] if active else
                                           [("Archive", "archive")]) + \
                ([("Stop", "stop")] if active else []) + [("Move to Trash…", "trash")]  # (Archive stops it: not at a turn)
        items.insert(1 if items[0][1] == "rename" else 0, ("Move to Group", None))  # (any chat's, on a host or not)
        for label, action in items:
            if action is None:
                menu.append_submenu(label, self.group_submenu(info))
                continue
            item = Gio.MenuItem.new(label, None)
            item.set_action_and_target_value(f"sb.{action}", sid)
            menu.append_item(item)
        self.menu = self.menu_at(menu, row, x, y)

    def group_submenu(self, info):
        """A chat's Move to Group: each group, its own ticked, then No Group; and New Group…, which it's moved to."""
        now = chat_group(info)
        self.actions.lookup_action("move_group").set_state(GLib.Variant("(ss)", (info["id"], now)))
        menu, known, new = Gio.Menu(), Gio.Menu(), Gio.Menu()
        for key, label, _note in self.known_groups():
            item = Gio.MenuItem.new(label.replace("_", "__") if key else "No Group", None)  # (no mnemonics)
            item.set_action_and_target_value("sb.move_group", GLib.Variant("(ss)", (info["id"], key)))
            known.append_item(item)
        item = Gio.MenuItem.new("New Group…", None)
        item.set_action_and_target_value("sb.new_group", GLib.Variant.new_string(info["id"]))
        new.append_item(item)
        menu.append_section(None, known)
        menu.append_section(None, new)
        return menu

    def on_key(self, _ctl, keyval, _code, _state):
        if keyval == Gdk.KEY_F2:
            row = self.list.get_focus_child() or self.list.get_selected_row()
            if row is not None and getattr(row, "info", None) and not row.info.get("unsaved") \
                    and not row.info.get("dead"):  # (a network session's renamed by its server: not while it's gone)
                self.ask_rename(row.info)
                return True
        return False

    def ask_rename(self, info):
        if info is None:
            return
        entry = Gtk.Entry(text=info.get("title") or "", activates_default=True)
        dlg = Adw.AlertDialog(heading="Rename session", extra_child=entry, default_response="rename",
                              close_response="cancel")
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("rename", "Rename")
        dlg.set_response_appearance("rename", Adw.ResponseAppearance.SUGGESTED)
        dlg.connect("response", lambda d, r: r == "rename" and self.rename(info, entry.get_text()))
        self.dialog = dlg
        dlg.present(self.win)
        entry.grab_focus()

    def rename(self, info, title):
        title = " ".join((title or "").split())
        if not title or title == info.get("title"):
            return
        win = self.win
        if (r := info.get("remote")) is not None:  # (on a server: its server renames it, and lists it so)
            r.request({"op": "rename", "session": info["id"], "title": title},
                      lambda reply: self.renamed_remote(r, info, title, reply))
            return
        if info["id"] == win.session_id and win.proc and not win.resume_pending:
            win.request({"subtype": "rename_session", "title": title}, lambda _r: None)  # the live session
        else:  # what the CLI itself writes for a rename
            with open(info["path"], "a", encoding="utf-8") as f:
                f.write(json.dumps({"type": "custom-title", "customTitle": title, "sessionId": info["id"]},
                                   separators=(",", ":")) + "\n")
        if info["id"] == win.session_id:
            win.show_title(title)
        self.refresh()

    def renamed_remote(self, r, info, title, reply):
        """A network session's server answered a rename: listed by its new title here before it next says what it has
        (the title it has there: its claude may have made it its own)."""
        if reply.get("op") != "renamed":
            toast(self, f"{remote_name(r)} didn't rename it: {reply.get('error')}")
            return
        listed = reply.get("session") or {}
        if listed.get("title") in (None, info.get("title")):  # (its claude's yet to write it: as asked, till it has)
            listed = {**listed, "title": title}
        if listed.get("id") == info["id"]:
            r.sessions[info["id"]] = listed
        if info["id"] == self.win.session_id and self.win.remote is r:
            self.win.show_title(listed["title"])
        self.fill()

    def archive(self, info):
        if info and info.get("remote") is not None:  # (a host's: archived there, for every head)
            self.ask_remote(info, "archive")
            return
        if info:
            st = load_settings()
            save_settings(archived=sorted(set(st.get("archived", [])) | {info["id"]}),
                          unarchived=sorted(set(st.get("unarchived", [])) - {info["id"]}))
            self.fill()

    def unarchive(self, info):
        if info and info.get("remote") is not None:  # (archived here, before hosts did, too: not any more)
            st = load_settings()
            if info["id"] in st.get("archived", []):
                save_settings(archived=sorted(set(st.get("archived", [])) - {info["id"]}))
            if info.get("archived"):
                self.ask_remote(info, "unarchive")
            else:
                self.fill()
            return
        if info:
            st = load_settings()  # (archived in Claude Desktop: shown here anyway from now on)
            save_settings(archived=sorted(set(st.get("archived", [])) - {info["id"]}),
                          unarchived=sorted(set(st.get("unarchived", [])) | {info["id"]}))
            self.fill()

    def ask_trash(self, info):
        if info is None:
            return
        n = info.get("copies", 0)
        r = info.get("remote")
        where = "your trash" if r is None or host_key(r) == HERE_HOST else f"the trash on {remote_name(r)}"
        dlg = Adw.AlertDialog(heading=f"Move “{info.get('title') or 'this session'}” to the trash?",
                              body="Its transcript" + (f" and its {n} earlier cop{'y' if n == 1 else 'ies'}"
                                                       if n else "") +
                                   f" will be moved to {where}, where you can restore it.",
                              default_response="cancel", close_response="cancel")
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("trash", "Move to Trash")
        dlg.set_response_appearance("trash", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.connect("response", lambda d, r: r == "trash" and self.trash(info))
        self.dialog = dlg
        dlg.present(self.win)

    def ask_remote(self, info, op):
        """A host's session archived, unarchived or deleted there (wire's archive, unarchive, delete): as it says it's
        done, listed so here; the open chat, if it's the one deleted, left for a new one."""
        r = info["remote"]

        def done(reply):
            if reply.get("op") == "error" and str(reply.get("error") or "").startswith("unknown op"):
                # (an older host process, without them: archived, or deleted, here only, as Baude did before)
                if op == "delete":
                    self.hide_remote(info)
                else:
                    st = load_settings()
                    ids = set(st.get("archived", []))
                    save_settings(archived=sorted(ids | {info["id"]} if op == "archive" else ids - {info["id"]}))
                    self.fill()
                return
            if reply.get("op") == "error":
                toast(self, f"{remote_name(r)} didn't {op} it: {reply.get('error')}")
                return
            if op == "delete":
                r.sessions.pop(info["id"], None)
                if info["id"] == self.win.session_id:
                    self.win.new_session()
            elif info["id"] in r.sessions:
                r.sessions[info["id"]] = {**r.sessions[info["id"]], "archived": op == "archive"}
            self.fill()
        r.request({"op": op, "session": info["id"]}, done)

    def trash(self, info):
        if info.get("remote") is not None:  # (a host's: moved to the trash there, by its host process)
            self.ask_remote(info, "delete")
            return
        for path in [info["path"], *info.get("copy_paths", [])]:
            try:
                Gio.File.new_for_path(path).trash(None)
            except GLib.Error:
                # no trash for this filesystem (e.g. /tmp): keep it recoverable in baude's own folder
                base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
                keep = os.path.join(base, "baude", "trash")
                os.makedirs(keep, exist_ok=True)
                try:
                    shutil.move(path, os.path.join(keep, os.path.basename(path)))
                    self.win.note(f"no trash here; moved {os.path.basename(path)} to {keep}")
                except OSError as e:
                    self.win.note(f"couldn't move {os.path.basename(path)} away: {e}")
        if info["id"] == self.win.session_id:
            self.win.new_session()
        self.refresh()

    def mine_listed(self):
        """Is a new chat here listed (This Mac's host, and No group, picked in ⋮: it's in no group till it's put in
        one)?"""
        hosts, groups = self.host_filter(), self.group_filter()
        return (hosts is None or HERE_HOST in hosts) and (groups is None or "" in groups)

    def new_chat(self):
        """New session: a chat here; or, while a new one here isn't listed (⋮: only other hosts, or only groups), one
        through a host that's listed (ask_remote_start: in the group picked, if one is), so it's listed too."""
        if self.mine_listed():
            self.win.switch_to(self.win.new_session)
        else:
            self.win.ask_remote_start(hosts=self.host_filter())

    def hide_remote(self, info):
        """Delete a network session that isn't at a turn: its transcript is its server's, so it's only not listed here
        till it shows up again (unhide_remote). Archive keeps it out of the list for good."""
        if info is None or info.get("remote") is None:
            return
        hidden = self.hidden_remote()
        hidden[info["id"]] = {"updated": info.get("mtime") or 0, "dead": bool(info.get("dead")), "at": time.time()}
        save_settings(hidden_remote=hidden)
        if info["id"] == self.win.session_id:
            self.win.new_session()
        self.fill()

    def stop_remote(self, info):
        """Stop a network session's claude, at its turn or not (its server keeps it, to be carried on with)."""
        if info is not None and info.get("remote") is not None:
            info["remote"].request({"op": "stop", "session": info["id"]})

    def set_state(self, state):
        """The open session's dot (the others follow their files: see file_state)."""
        row = self.list.get_selected_row()
        if row is not None and getattr(row, "dot", None) is not None:
            row.dot.set_state(state)

    def file_state(self, info):
        """A session baude isn't running: working while another program (Claude Desktop, a
        terminal) is in the middle of a turn in it, done for a minute after, else inactive. One left mid-turn, its
        claude going on in the background: working, or waiting while it asks you something."""
        if (p := background_proc(info.get("id"))) is not None:
            return "waiting" if p.asks else "working"
        if info.get("remote") is not None:  # (a network session: as its server last said)
            return "inactive" if info.get("dead") else \
                {"working": "working", "waiting": "waiting"}.get(info.get("remote_state"), "inactive")
        path = info.get("path")
        try:
            mtime = os.stat(path).st_mtime
        except (OSError, TypeError):
            return "inactive"
        quiet = time.time() - mtime
        if quiet > TURN_OPEN_SECONDS:
            return "inactive"
        cached = self.turn_cache.get(path)
        if cached is None or cached[0] != mtime:
            cached = self.turn_cache[path] = (mtime, transcript_turn_open(path))
        return external_state(quiet, cached[1])

    def watch_rows(self):
        """File events for the listed sessions, so a dot changes the moment its session does
        (no polling); a timer only runs while some dot needs to expire after its writes stop."""
        rows = {r.info["path"]: r for r in self.session_rows() if r.info.get("remote") is None}
        for path in set(self.monitors) - set(rows):
            self.monitors.pop(path).cancel()
        for path in set(rows) - set(self.monitors):
            try:
                mon = Gio.File.new_for_path(path).monitor_file(Gio.FileMonitorFlags.NONE, None)
            except GLib.Error:
                continue
            mon.set_rate_limit(250)
            mon.connect("changed", lambda *_a, p=path: self.restate(p))
            self.monitors[path] = mon
        self.schedule_expiry()

    def session_rows(self):
        row, out = self.list.get_first_child(), []
        while row is not None:
            info = getattr(row, "info", None)
            if info and info.get("path") and not info.get("unsaved") and info["id"] != self.win.session_id:
                out.append(row)
            row = row.get_next_sibling()
        return out

    def restate(self, path=None):
        busy = False
        for row in self.session_rows():
            if path is None or row.info["path"] == path:
                row.dot.set_state(self.file_state(row.info))
            busy = busy or row.dot.state in ("working", "done")
        return busy

    def schedule_expiry(self):
        if self.expiry is None and self.restate():
            def tick():
                if self.restate():
                    return True
                self.expiry = None
                return False
            self.expiry = GLib.timeout_add_seconds(5, tick)

    def on_activate(self, _list, row):
        if getattr(row, "more_key", None):
            self.expanded.add(row.more_key)
            self.fill()
            return
        info = getattr(row, "info", None)
        if info is None or info.get("unsaved") or info.get("ghost") or info["id"] == self.win.session_id:
            return  # (a ghost: a chat Meta's deleted, its transcript in Baude's trash)
        if info.get("remote") is not None:  # (its transcript fetched first: open_remote_soon)
            self.win.open_remote_soon(info)
            return
        self.win.opening_remote = None  # (another picked meanwhile: not opened after all)
        self.win.switch_to(lambda: self.win.resume_session(info))

    def focus_search(self):
        self.search.grab_focus()


# --------------------------------------------------------- side questions --

class SideQuestion(Gtk.Box):
    """One /btw thread: its own claude, started with
        --resume <session> --fork-session --no-session-persistence --permission-mode plan
    so it sees the whole conversation so far but writes nothing (not to the session, not a new
    one), and can't edit anything. Tools that need permission are refused. Follow-ups go to the
    same process; closing the thread ends it. Any number can run at once."""

    PREFACE = ("(This is a side question, asked while you work on the conversation above. Answer it "
               "briefly from what you already know; don't continue the task or use tools.)\n\n")

    def __init__(self, win, question):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.add_css_class("side-question")
        self.win, self.busy, self.answer = win, False, None
        head = Gtk.Box(spacing=8)
        self.dot = StatusDot("working")
        head.append(self.dot)
        title = Gtk.Label(label=question, xalign=0, hexpand=True, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR)
        title.add_css_class("heading")
        head.append(title)
        close = Gtk.Button(icon_name="window-close-symbolic", tooltip_text="Close this side question",
                           valign=Gtk.Align.START)
        close.add_css_class("flat")
        close.connect("clicked", lambda *_: self.close())
        head.append(close)
        self.append(head)
        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.append(self.body)
        self.follow = Gtk.Entry(placeholder_text="Ask a follow-up…", sensitive=False)
        self.follow.connect("activate", lambda e: self.ask(e.get_text()) or e.set_text(""))
        self.append(self.follow)
        if not win.runs_here():  # (a chat of Baude's, or a host's: its side question's the host process's too)
            self.proc, self.forked = win.side_question_proc(self.on_msg, self.on_exit)
        else:
            info = find_session(win.session_id) if win.session_id else None
            fork = ["--fork-session"] if info else []  # nothing saved yet: a fresh, context-free side chat
            self.forked = bool(info)
            self.proc = ClaudeProc(self.on_msg, self.on_exit, win.run_cwd, win.model, "plan",
                                   info["id"] if info else None, None, extra=fork + ["--no-session-persistence"])
        self.ask(question, first=True)

    def ask(self, text, first=False):
        text = (text or "").strip()
        if not text or self.busy or self.proc is None:
            return
        if not first:  # (a follow-up: in a bubble, as the chat has what you wrote; the answer in none)
            self.body.append(self.win.user_bubble(text, []))
        self.answer = MarkdownView()
        self.body.append(self.answer)
        self.busy = True
        self.dot.set_state("working")
        self.follow.set_sensitive(False)
        self.proc.user((self.PREFACE if first else "") + text)

    def on_msg(self, proc, msg):
        if proc is not self.proc:
            return False
        t = msg.get("type")
        if t == "stream_event" and not msg.get("parent_tool_use_id"):
            d = msg.get("event", {}).get("delta") or {}
            if d.get("type") == "text_delta" and self.answer is not None:
                self.answer.append(d["text"])
        elif t == "assistant" and not msg.get("parent_tool_use_id") and self.answer is not None \
                and not self.answer.text:
            for c in msg.get("message", {}).get("content", []):
                if c.get("type") == "text":
                    self.answer.append(c["text"])
        elif t == "control_request" and msg.get("request", {}).get("subtype") == "can_use_tool":
            self.proc.respond(msg["request_id"], {"behavior": "deny",
                                                  "message": "Side questions can't run tools that need permission."})
        elif t == "result":
            self.busy = False
            self.dot.set_state("done")
            self.follow.set_sensitive(True)
            if msg.get("is_error") and self.answer is not None and not self.answer.text:
                self.answer.append(f"*{msg.get('result') or 'The side question failed.'}*")
        return False

    def on_exit(self, code):
        if self.proc is not None:
            self.busy = False
            self.dot.set_state("inactive")
            self.follow.set_sensitive(False)
        return False

    def close(self):
        proc, self.proc = self.proc, None
        if proc:
            proc.kill()
        if self.get_parent():
            self.get_parent().remove(self)
        self.win.panel.side_questions_changed()


# ------------------------------------------------------------- side panel --

# ------------------------------------------------------------------ Meta --
# Meta (meta.py, Record's too), the side panel's first page: Baude's helper, which changes the sidebar (groups, hosts
# listed, archiving, deleting and renaming chats, how they're grouped) and the settings, each change an action under
# its reply, undone there, and marked in the sidebar till it's kept or undone (SessionSidebar.session_row).

BAUDE_PROMPT = """Chats: each has an id (its first 8 characters will do) and a title, and is in a folder, on a host and \
in a group. Hosts are the machines chats run on; groups are the user's own division of the chats into whole lists. \
⋮ in the left bar picks which are listed: show_hosts and show_groups (none given: all of them). move_chats puts chats \
in a group (made if it's new; "" for none, whatever host they're on); make_group, rename_group and delete_group change \
the groups. highlight_chats highlights chats in the list without changing them: use it to show which you mean before a \
change the user should check. archive_chats and unarchive_chats; delete_chats moves chats of this machine's own (not \
a host's sessions) to Baude's trash, where undo finds them; a host's session you don't delete: show button:trash. How \
the list is grouped (by folder or by day) is the setting group_by."""

BAUDE_TOOLS = [
    {"name": "highlight_chats", "description": "Highlight chats in the left bar, changing nothing, to show the user "
     "which you mean (till their next message).",
     "inputSchema": {"type": "object", "required": ["chats"], "properties": {
         "chats": {"type": "array", "items": {"type": "string"}}}}},
    {"name": "move_chats", "description": "Put chats in a group (a new name makes it; \"\" for no group).",
     "inputSchema": {"type": "object", "required": ["chats", "group"], "properties": {
         "chats": {"type": "array", "items": {"type": "string"}}, "group": {"type": "string"}}}},
    {"name": "make_group", "description": "Make a group, empty till chats are moved to it.",
     "inputSchema": {"type": "object", "required": ["name"], "properties": {"name": {"type": "string"}}}},
    {"name": "rename_group", "description": "Rename a group; to a group there already, its chats join that one.",
     "inputSchema": {"type": "object", "required": ["group", "name"], "properties": {
         "group": {"type": "string"}, "name": {"type": "string"}}}},
    {"name": "delete_group", "description": "Delete a group; its chats are in no group then.",
     "inputSchema": {"type": "object", "required": ["group"], "properties": {"group": {"type": "string"}}}},
    {"name": "show_groups", "description": "Which groups the left bar lists (\"No group\" for chats in none; none "
     "given: all).",
     "inputSchema": {"type": "object", "required": ["groups"], "properties": {
         "groups": {"type": "array", "items": {"type": "string"}}}}},
    {"name": "show_hosts", "description": "Which hosts the left bar lists, by name (none given: all).",
     "inputSchema": {"type": "object", "required": ["hosts"], "properties": {
         "hosts": {"type": "array", "items": {"type": "string"}}}}},
    {"name": "archive_chats", "description": "Archive chats: listed under Archived, out of the way.",
     "inputSchema": {"type": "object", "required": ["chats"], "properties": {
         "chats": {"type": "array", "items": {"type": "string"}}}}},
    {"name": "unarchive_chats", "description": "Bring archived chats back to the list.",
     "inputSchema": {"type": "object", "required": ["chats"], "properties": {
         "chats": {"type": "array", "items": {"type": "string"}}}}},
    {"name": "delete_chats", "description": "Delete chats of this machine's own: their transcripts go to Baude's "
     "trash, and undo brings them back. Not a host's session, nor the chat open.",
     "inputSchema": {"type": "object", "required": ["chats"], "properties": {
         "chats": {"type": "array", "items": {"type": "string"}}}}},
]
META_KEEP = 400  # messages and actions kept in meta.json, each (the oldest go)
_META = []


def meta_path():
    return os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"), "baude", "meta.json")


def meta_store():
    """Baude's conversation with Meta: its messages and its actions (the journal undo works from), one for the app,
    every window's, kept in Baude's data folder's meta.json."""
    if not _META:
        try:
            with open(meta_path()) as f:
                kept = json.load(f)
        except (OSError, ValueError):
            kept = {}
        _META.append({"messages": list(kept.get("messages") or []), "actions": list(kept.get("actions") or [])})
    return _META[0]


def save_meta_store():
    store, path = meta_store(), meta_path()
    for key in ("messages", "actions"):
        del store[key][:-META_KEEP]
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + ".tmp", "w") as f:
            json.dump(store, f, ensure_ascii=False)
        os.replace(path + ".tmp", path)
    except OSError as e:
        print(f"baude: Meta's conversation not kept: {e}", file=sys.stderr)


def meta_trash():
    """Where Meta's delete_chats puts transcripts: Baude's own trash folder (Move to Trash's, where there's no
    system trash), a folder each."""
    return os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"), "baude", "trash")


class MetaNames(lookup.SearchSpace):
    """What @ lists in Meta's box: Baude's chats by their titles (the sidebar's), then the names the open chat's
    folder has (the glossary's search: SearchSpace, of catalogue_signal)."""

    def __init__(self, win):
        super().__init__(None, GLib.timeout_add)
        self.win, self._chats, self._space = win, (None, []), None

    def folder_space(self):
        signal = self.win.catalogue_signal()
        if signal is None:
            return None
        if self._space is None or self._space.source is not signal:
            self._space = SearchSpace(signal)
        return self._space

    @property
    def root(self):
        return self.win.cwd

    @property
    def busy(self):
        return (space := self.folder_space()) is not None and space.busy

    def refresh(self, on_ready=None):
        return space.refresh(on_ready) if (space := self.folder_space()) is not None else None

    def chat_items(self):
        sessions = self.win.sidebar.sessions if getattr(self.win, "sidebar", None) is not None else []
        if self._chats[0] is not sessions:
            items = []
            for info in sorted(sessions, key=last_used, reverse=True):
                if not info.get("title"):
                    continue
                at, where = last_used(info), pretty_path(info.get("folder") or info.get("cwd") or "") or "no folder"
                items.append({"surface": info["title"], "canonical": "", "icon": "claude", "what": "Chat",
                              "kind": "chat", "at": at, "ago": (ago := time_ago(at)), "where": where,
                              "summary": f"{where} · {ago}", "path": info.get("path"), "id": info["id"], "line": None,
                              "entry": None, "faint": False})
            self._chats = (sessions, items)
        return self._chats[1]

    def items(self):
        space = self.folder_space()
        parts = (self.chat_items(), space.items() if space is not None else [])
        if self._items is None or any(a is not b for a, b in zip(parts, getattr(self, "_parts", ()))):
            self._items, self._parts, self._by = parts[0] + parts[1], parts, {}
            for it in self._items:
                self._by.setdefault(it["surface"], it)
        return self._items


class BaudeMeta(meta.MetaChat):
    """Meta in Baude (meta.MetaChat), the side panel's first page: one conversation for the app (meta_store), each
    window's session its own, started in its chat's folder. Its tools change the sidebar (BAUDE_TOOLS) and the
    settings; what it changes in the list is marked there (SessionSidebar.session_row), a chat it deletes listed still,
    struck through, till that's kept or undone (ghosts)."""

    APP = "Baude"
    WHAT = "a client for Claude Code chats, listed in its left bar."
    LISTED = "the chats"
    CHAT_ROWS = True
    every = weakref.WeakSet()  # (each window's: the conversation's theirs too, shown again as it changes)

    def __init__(self, win):
        super().__init__(win)
        self.names = MetaNames(win)
        self.settings = self.make_settings()
        BaudeMeta.every.add(self)
        self.render()

    # ---- what meta.MetaChat asks of Baude
    def messages(self):
        return meta_store()["messages"]

    def actions(self):
        return meta_store()["actions"]

    def save(self, now=False):
        save_meta_store()
        for other in list(BaudeMeta.every):  # (another window's, not at a turn: shown as it is now)
            if other is not self and other.ask is None and not getattr(other.win, "let_go_done", False):
                GLib.idle_add(other.render)

    def told(self):
        return meta_store().setdefault("told", [])

    def space(self):
        return self.names

    def scope(self):
        return self.win.cwd

    def agent_setup(self):
        cwd = self.win.cwd if self.win.cwd and os.path.isdir(self.win.cwd) else os.path.expanduser("~")
        return {"cwd": cwd, "roots": [self.win.cwd] if self.win.cwd else [], "exe": claude_exe()}

    def app_prompt(self):
        return BAUDE_PROMPT

    def app_tools(self):
        return BAUDE_TOOLS

    def app_handlers(self):
        return {"highlight_chats": self.highlight_chats, "move_chats": self.move_chats, "make_group": self.make_group,
                "rename_group": self.rename_group, "delete_group": self.delete_group, "show_groups": self.show_groups,
                "show_hosts": self.show_hosts, "archive_chats": lambda t, a: self.archive_chats(t, a, True),
                "unarchive_chats": lambda t, a: self.archive_chats(t, a, False), "delete_chats": self.delete_chats}

    def make_settings(self):
        win = self.win
        out = meta.shared_settings(win, self, "Baude", lambda: win.zoom_default)
        by = ["folder", "date"]

        def group_by(v):
            if (sidebar := getattr(win, "sidebar", None)) is not None:
                sidebar.actions.lookup_action("group").change_state(GLib.Variant.new_string(v))
            else:
                save_settings(sidebar_group=v)

        def row():
            r = Adw.ComboRow(title="Group by", model=Gtk.StringList.new(["Folder", "Date"]),
                             selected=by.index(now()))
            r.connect("notify::selected", lambda r, _p: group_by(by[r.get_selected()]))
            return r
        now = lambda: "date" if load_settings().get("sidebar_group") == "date" else "folder"  # noqa: E731
        out["group_by"] = meta.Setting("group_by", "Group by", now, group_by, row, by,
                                       note="the left bar's chats, under their folders or their days")
        return out

    def marks_changed(self):
        if (sidebar := getattr(self.win, "sidebar", None)) is not None:
            sidebar.fill()

    def ghosts(self):
        """The chats Meta's deleted and that aren't kept so: listed still, struck through, where they were."""
        return [{**info, "ghost": True} for a in self.unkept() for p in meta.action_parts(a)
                if p["kind"] == "delete_chats" for info in p["redo"].get("infos") or ()]  # (a bundle's parts too)

    # ---- chats
    def all_infos(self):
        """Every chat Baude knows: this machine's own (the sidebar's), and the hosts' sessions."""
        net = [remote_info(s) for s in network().sessions() if not s.get("draft")]  # (a new chat not written in yet)
        theirs = {i for s in network().sessions() for i in (s["id"], s.get("cli_id")) if i}
        sidebar = getattr(self.win, "sidebar", None)
        return net + [i for i in (sidebar.sessions if sidebar is not None else []) if i["id"] not in theirs]

    def find_chat(self, ref):
        ref = ref.strip().removeprefix("s:")
        if not ref:
            return None
        infos = self.all_infos()
        for test in (lambda i: i["id"] == ref, lambda i: len(ref) >= 4 and i["id"].startswith(ref),
                     lambda i: (i.get("title") or "").lower() == ref.lower()):
            found = [i for i in infos if test(i)]
            if len(found) == 1:
                info = found[0]
                return {"key": info["id"], "title": info.get("title") or "(untitled)", "path": info.get("path"),
                        "info": info}
        return None

    def _infos(self, refs):
        infos = [self._chat(ref)["info"] for ref in refs or ()]
        if not infos:
            raise ValueError("Which chats? chats is empty.")
        return infos

    @staticmethod
    def said(infos):
        titles = [f"“{i.get('title') or '(untitled)'}”" for i in infos]
        return titles[0] if len(titles) == 1 else f"{len(titles)} chats"

    def open_chat(self, info):
        """A chat's row clicked (show chats): it's opened, as the sidebar opens one."""
        win = self.win
        if info["id"] == win.session_id:
            return
        if info.get("remote") is not None:
            win.open_remote_soon(info)
        else:
            win.switch_to(lambda: win.resume_session(info))

    def chats_rows(self, refs):
        rows, assigned = [], chat_groups()
        for ref in refs:
            info = self._chat(ref)["info"]
            group = chat_group(info, assigned)
            rows.append({"icon": "baude-claude-symbolic", "title": info.get("title") or "(untitled)",
                         "subtitle": " · ".join(filter(None, [pretty_path(info.get("folder") or info.get("cwd") or ""),
                                                             time_ago(last_used(info)), group and f"group {group}"])),
                         "open": lambda info=info: self.open_chat(info)})
        return rows

    def rename_chat_to(self, chat, title):
        self.win.sidebar.rename({**chat["info"], "title": None}, title)  # (its title as listed may be one behind)

    def forms(self):
        win = self.win

        def spawn():  # (this machine's host first; none there: it, started as the agent's started)
            return win.remote_start_form(sorted(network().alive(), key=lambda r: host_key(r) != HERE_HOST) or [None],
                                         agents=True)
        return {"add_host": ("add a host Baude reaches over ssh", win.host_form),
                "new_group": ("make a group of chats", win.group_form),
                "spawn_agent": ("start an agent (a session) on a host: the host, then what it offers to run, the "
                                "folder, group and prompt", spawn)}

    def open_mark(self, kind, key, how, action):
        """A chat in a change's box clicked: opened, as the sidebar opens one; one deleted is in Baude's trash."""
        if (chat := self.find_chat(key)) is not None:
            self.open_chat(chat["info"])
        elif how == "removed":
            toast(self, "It's in Baude's trash: undo brings it back")

    def chat_text(self, chat):
        path = chat.get("path")
        return meta.transcript_text(path) if path and os.path.exists(path) else "(its transcript isn't here)"

    def buttons(self):
        return {"trash": "Move to Trash… a chat (items: its id), to the system's trash or its host's; asks first, "
                         "and can't be undone here"}

    def button(self, action, items):
        if action != "trash" or not items:
            raise ValueError(f"There's no button {action}{'' if items else ' without items'}: app_index buttons.")
        info = self._chat(items[0])["info"]
        btn = Gtk.Button(label=f"Move “{info.get('title') or '(untitled)'}” to Trash…")
        btn.add_css_class("destructive-action")
        btn.connect("clicked", lambda *_: self.win.sidebar.ask_trash(self.find_chat(info["id"])["info"]
                                                                     if self.find_chat(info["id"]) else info))
        return btn

    # ---- its tools
    def highlight_chats(self, turn, args):
        infos = self._infos(args.get("chats"))
        self.lit = {**self.lit, "chat": {i["id"] for i in infos}}
        self.marks_changed()
        return {"text": f"Highlighted {self.said(infos)} in the left bar."}

    def move_chats(self, turn, args):
        infos, group = self._infos(args.get("chats")), " ".join(str(args.get("group") or "").split())
        sidebar = self.win.sidebar
        made = bool(group) and group not in {k for k, *_ in sidebar.known_groups()}
        if made:
            sidebar.make_group(group)
        ids = [i["id"] for i in infos]
        before = sidebar.move_to_group(ids, group)
        where = f"group {group}" if group else "no group"
        a = self.act(turn, f"Moved {self.said(infos)} to {where}", "move_chats",
                     {"before": before, "group": group, "made": made}, {"ids": ids, "group": group, "made": made},
                     [("chat", i, "moved", f"→ {group or 'no group'}") for i in ids])
        return {"text": f"Moved to {where} (action {a['id']})."}

    def make_group(self, turn, args):
        name = " ".join(str(args.get("name") or "").split())
        if not name:
            raise ValueError("A group's name is needed.")
        if name in {k for k, *_ in self.win.sidebar.known_groups()}:
            return {"text": f"There's a group {name} already."}
        self.win.sidebar.make_group(name)
        a = self.act(turn, f"Made group {name}", "groups", {"op": "unmake", "name": name}, {"op": "make", "name": name})
        return {"text": f"Made {name} (action {a['id']})."}

    def _group(self, name):
        name = " ".join(str(name or "").split())
        if name.lower() == "no group":
            raise ValueError("No group isn't a group: it's the chats in none.")
        if name not in {k for k, *_ in self.win.sidebar.known_groups()}:
            raise ValueError(f"No group {name!r}: app_index groups lists them.")
        return name

    def rename_group(self, turn, args):
        old, new = self._group(args.get("group")), " ".join(str(args.get("name") or "").split())
        if not new or new == old:
            raise ValueError("A new name is needed.")
        state = self.win.sidebar.rename_group(old, new)
        undo = {"op": "unrename", "old": old, "new": new, "entries": state["chat_groups"],
                "made": old in state["group_names"], "new_made": new in state["group_names"],
                "picked": old in (state["sidebar_groups"] or ()), "new_picked": new in (state["sidebar_groups"] or ())}
        a = self.act(turn, f"Renamed group {old} to {new}", "groups", undo, {"op": "rename", "old": old, "new": new})
        return {"text": f"Renamed (action {a['id']})."}

    def delete_group(self, turn, args):
        name = self._group(args.get("group"))
        state = self.win.sidebar.delete_group(name)
        undo = {"op": "undelete", "name": name, "entries": state["chat_groups"], "made": name in state["group_names"],
                "picked": name in (state["sidebar_groups"] or ())}
        a = self.act(turn, f"Deleted group {name}", "groups", undo, {"op": "delete", "name": name})
        return {"text": f"Deleted {name}; its chats are in no group (action {a['id']})."}

    def _filter(self, turn, key, said, known, names):
        """Which hosts or groups are listed (⋮), by their names (known: [(key, name, note)]); none: all."""
        by_name = {name.lower(): k for k, name, _note in known}
        keys = []
        for n in names or ():
            if str(n).strip().lower() not in by_name:
                raise ValueError(f"No {said} {n!r}: they're {', '.join(name for _k, name, _n in known)}.")
            keys.append(by_name[str(n).strip().lower()])
        before = load_settings().get(key)
        self.win.sidebar.filtered(**{key: sorted(set(keys)) or None})
        now = load_settings().get(key)
        if now == before:
            return {"text": "That's what's listed already."}
        a = self.act(turn, f"Listed {', '.join(map(str, names)) if keys else f'every {said}'}", "filter",
                     {"key": key, "value": before, "expect": now}, {"key": key, "value": now, "expect": before})
        return {"text": f"Listed (action {a['id']})."}

    def show_groups(self, turn, args):
        return self._filter(turn, "sidebar_groups", "group", self.win.sidebar.known_groups(), args.get("groups"))

    def show_hosts(self, turn, args):
        return self._filter(turn, "sidebar_hosts", "host", self.win.sidebar.known_hosts(), args.get("hosts"))

    def archive_chats(self, turn, args, archive):
        sidebar, archived = self.win.sidebar, self.win.sidebar.archived_ids()
        infos = [i for i in self._infos(args.get("chats"))
                 if (i["id"] in archived or bool(i.get("archived"))) != archive]
        if not infos:
            return {"text": f"They're {'archived' if archive else 'listed'} already."}
        busy = [i for i in infos if (i.get("remote_state") if i.get("remote") is not None else
                                     sidebar.file_state(i)) in ("working", "waiting")]
        if archive and busy:
            raise ValueError(f"{self.said(busy)} {'is' if len(busy) == 1 else 'are'} at a turn: archiving would stop "
                             "it, which can't be undone. Say so to the user.")
        ids, st = [i["id"] for i in infos], load_settings()
        lists = {i["id"]: [i["id"] in st.get("archived", []), i["id"] in st.get("unarchived", [])]
                 for i in infos if i.get("remote") is None}  # (this machine's: put back as they were, undone)
        self.apply_app("archive", {"ids": ids, "archived": archive, "expect": not archive})
        a = self.act(turn, f"{'Archived' if archive else 'Unarchived'} {self.said(infos)}", "archive",
                     {"ids": ids, "archived": not archive, "lists": lists, "expect": archive},
                     {"ids": ids, "archived": archive, "expect": not archive},
                     [("chat", i, "archived" if archive else "added", "archived" if archive else "back") for i in ids])
        return {"text": f"{'Archived' if archive else 'Unarchived'} (action {a['id']})."}

    def delete_chats(self, turn, args):
        infos = self._infos(args.get("chats"))
        if hosted := [i for i in infos if i.get("remote") is not None]:
            raise ValueError(f"{self.said(hosted)}: a host's session, whose deleting can't be undone here. Show "
                             "button:trash for the user instead.")
        if desktop := [i for i in infos if is_desktop(i["id"])]:
            raise ValueError(f"{self.said(desktop)}: Claude Desktop's, which keeps its own record of it. Show "
                             "button:trash for the user instead.")
        if any(i["id"] == self.win.session_id for i in infos):
            raise ValueError("One of them is the chat open: the user switches to another first.")
        recent = [i for i in infos if time.time() - (os.path.getmtime(i["path"]) if os.path.exists(i["path"]) else 0)
                  < self.DELETE_QUIET]
        if recent:
            raise ValueError(f"{self.said(recent)}: used in the last {self.DELETE_QUIET // 60} minutes, perhaps open "
                             "elsewhere (another window, a terminal), which would write it again. Say so to the user.")
        folder = os.path.join(meta_trash(), f"meta-{uuid.uuid4().hex[:10]}")
        moved = [[path, os.path.join(folder, info["id"], os.path.basename(path))]
                 for info in infos for path in [info["path"], *info.get("copy_paths", [])] if os.path.exists(path)]
        keep = ("id", "title", "cwd", "folder", "worktree", "mtime", "said", "prompts", "desktop_used")
        infos_kept = [{k: i[k] for k in keep if k in i} for i in infos]
        self.apply_app("delete_chats", {"moved": moved})
        a = self.act(turn, f"Deleted {self.said(infos)}", "delete_chats",
                     {"moved": [[dst, src] for src, dst in moved]}, {"moved": moved, "infos": infos_kept},
                     [("chat", i["id"], "removed", "deleted") for i in infos])
        return {"text": f"Deleted: in Baude's trash; undo brings {'it' if len(infos) == 1 else 'them'} back "
                        f"(action {a['id']})."}

    DELETE_QUIET = 600  # s since a chat's transcript was last written before delete_chats takes it

    def _groups_put(self, assigned=None, names=None, picked=...):
        """chat_groups, group_names and sidebar_groups as given (any left out as they are), and the list shown so."""
        changes = {}
        if assigned is not None:
            changes["chat_groups"] = assigned or None
        if names is not None:
            changes["group_names"] = names or None
        if picked is not ...:
            changes["sidebar_groups"] = picked
        save_settings(**changes)
        self.win.sidebar.groups_changed()

    def _entries_back(self, entries, now):
        """Chats' chat_groups entries put back (entries: {id: entry, None: none}), each only while it's now (else
        ValueError: moved since)."""
        assigned, moved = chat_groups(), 0
        for i in entries:
            chat = self.find_chat(i)
            moved += chat_group(chat["info"] if chat else {"id": i}, assigned) != now
        if moved:
            raise ValueError(f"{moved} of its chats moved since")
        for i, entry in entries.items():
            if entry is None:
                assigned.pop(i, None)
            else:
                assigned[i] = entry
        return assigned

    def apply_app(self, kind, data):
        sidebar = self.win.sidebar
        st = load_settings()
        names, picked = made_groups(st), st.get("sidebar_groups") if isinstance(st.get("sidebar_groups"), list) else None
        if kind == "move_chats":
            if "before" in data:  # (undone: each chat back where it was, while it's still where it was moved to)
                assigned = self._entries_back(data["before"], data["group"])
                if data.get("made") and data["group"] not in assigned.values():
                    names = [g for g in names if g != data["group"]]
                self._groups_put(assigned, names)
            else:
                if data["group"] and data["group"] not in {k for k, *_ in sidebar.known_groups()}:
                    sidebar.make_group(data["group"])
                sidebar.move_to_group(data["ids"], data["group"])
        elif kind == "groups":
            op = data["op"]
            if op == "make":
                sidebar.make_group(data["name"])
            elif op == "unmake":
                self._groups_put(names=[g for g in names if g != data["name"]])
            elif op == "rename":
                sidebar.rename_group(data["old"], data["new"])
            elif op == "delete":
                sidebar.delete_group(data["name"])
            elif op == "unrename":
                old, new = data["old"], data["new"]
                assigned = self._entries_back(data["entries"], new)
                names = [g for g in names if g != new or data["new_made"]] + \
                    ([old] if data["made"] and old not in names else [])
                if picked is not None and data["picked"]:
                    picked = sorted({*picked, old} - (set() if data["new_picked"] else {new}))
                self._groups_put(assigned, names, picked)
            elif op == "undelete":
                name = data["name"]
                assigned = self._entries_back(data["entries"], "")
                if data["made"] and name not in names:
                    names = names + [name]
                if picked is not None and data["picked"] and name not in picked:
                    picked = sorted({*picked, name})
                self._groups_put(assigned, names, picked)
        elif kind == "filter":
            if load_settings().get(data["key"]) != data["expect"]:
                raise ValueError("what's listed changed since")
            sidebar.filtered(**{data["key"]: data["value"]})
        elif kind == "archive":
            archived_now = sidebar.archived_ids()
            for i in data["ids"]:
                if (chat := self.find_chat(i)) is None:
                    raise ValueError("that chat isn't here any more")
                if (i in archived_now or bool(chat["info"].get("archived"))) != data["expect"]:
                    raise ValueError("archived or unarchived since")
            lists = data.get("lists") or {}
            if lists:  # (this machine's chats: archived and unarchived as they were)
                archived, unarchived = set(st.get("archived", [])), set(st.get("unarchived", []))
                for i, (was_archived, was_unarchived) in lists.items():
                    (archived.add if was_archived else archived.discard)(i)
                    (unarchived.add if was_unarchived else unarchived.discard)(i)
                save_settings(archived=sorted(archived), unarchived=sorted(unarchived))
                sidebar.fill()
            for i in data["ids"]:
                if i in lists:
                    continue
                if (chat := self.find_chat(i)) is None:
                    raise ValueError("that chat isn't here any more")
                (sidebar.archive if data["archived"] else sidebar.unarchive)(chat["info"])
        elif kind == "delete_chats":
            if clash := [dst for _src, dst in data["moved"] if os.path.exists(dst)]:
                raise ValueError(f"{clash[0]} is there again (the chat was written to since); what was deleted is "
                                 f"still at {data['moved'][0][0]}")
            done = []
            try:
                for src, dst in data["moved"]:
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.move(src, dst)
                    done.append((src, dst))
            except OSError as e:  # (part way: what was moved, put back)
                for src, dst in reversed(done):
                    with contextlib.suppress(OSError):
                        shutil.move(dst, src)
                raise ValueError(f"couldn't move {os.path.basename(src)}: {e}")
            for src, _dst in data["moved"]:  # (the folders a chat's left in Baude's trash, empty now: gone)
                if src.startswith(meta_trash() + os.sep):
                    for folder in (os.path.dirname(src), os.path.dirname(os.path.dirname(src))):
                        with contextlib.suppress(OSError):
                            os.rmdir(folder)
            sidebar.refresh()
        else:
            raise ValueError(f"No such action: {kind}")

    # ---- the index: Baude's part
    def index_lines(self):
        win, sidebar, lines = self.win, self.win.sidebar, []
        if win.session_id and win.chat_begun():
            lines.append(f"chat open: {win.session_id[:8]} {json.dumps(getattr(win, 'session_title', None) or '')} · "
                         f"{pretty_path(win.cwd or '')} · {win.model or 'default model'} · {win.mode} · "
                         f"{win.session_state()}")
        infos, assigned = self.all_infos(), chat_groups()
        archived = sidebar.archived_ids()
        counts = {}
        for i in infos:
            counts[chat_group(i, assigned)] = counts.get(chat_group(i, assigned), 0) + 1
        hosts, groups = sidebar.host_filter(), sidebar.group_filter()
        lines.append("hosts: " + ", ".join(name + (f" ({note})" if note else "") for _k, name, note in
                                           sidebar.known_hosts()) +
                     f" · listed: {'all' if hosts is None else ', '.join(sorted(hosts))}")
        lines.append("groups: " + ", ".join(f"{label} {counts.get(k, 0)}" for k, label, _n in sidebar.known_groups()) +
                     f" · listed: {'all' if groups is None else ', '.join(g or 'No group' for g in sorted(groups))}")
        lines.append(f"chats: {len(infos)}, {sum(i['id'] in archived or bool(i.get('archived')) for i in infos)} "
                     f"archived; grouped by {'date' if sidebar.by_date() else 'folder'} (app_index chats, filter by a "
                     "word, a folder or a group)")
        return lines

    def running_lines(self):
        win, out = self.win, []
        if win.proc is not None:
            pid = getattr(getattr(win.proc, "p", None), "pid", None)
            out.append(f"the open chat's claude ({win.session_state()}" + (f", pid {pid})" if pid else ")"))
        if busy := [s for s in network().sessions() if s.get("state") in ("working", "waiting")]:
            out.append(f"{len(busy)} host session{'s' if len(busy) != 1 else ''} at a turn: " +
                       ", ".join(f"{s['id'][:8]} {s.get('state')}" for s in busy[:8]))
        panel = win.panel
        for n, said in ((len(panel.agent_rows), "subagent"), (len(panel.proc_rows), "background process"),
                        (len(panel.side_questions()), "side question")):
            if n:
                out.append(f"{n} {said}{'s' if n != 1 else ''} in the open chat")
        if self.agent is not None and not self.agent.closed:
            out.append(f"{meta.NAME}'s {self.agent.runs} (pid {self.agent.proc.p.pid}, {self.agent.model}"
                       f"{f', {self.agent.effort} effort' if self.agent.effort else ''})")
        return out

    def sections(self):
        sidebar = self.win.sidebar

        def chats(_f):
            assigned, archived = chat_groups(), sidebar.archived_ids()
            return [{"id": i["id"][:8], "title": i.get("title") or "(untitled)",
                     "folder": pretty_path(i.get("folder") or i.get("cwd") or "") or "no folder",
                     "group": chat_group(i, assigned),
                     "host": remote_name(i["remote"]) if i.get("remote") and host_key(i["remote"]) != HERE_HOST else
                     THIS_MACHINE, "ago": time_ago(last_used(i)),
                     "state": i.get("remote_state") if i.get("remote") is not None else sidebar.file_state(i),
                     "archived": i["id"] in archived or bool(i.get("archived"))}
                    for i in sorted(self.all_infos(), key=last_used, reverse=True)]

        def groups(_f):
            picked = sidebar.group_filter()
            return [{"name": label, "listed": picked is None or k in picked} for k, label, _n in sidebar.known_groups()]

        def hosts(_f):
            picked = sidebar.host_filter()
            return [{"name": name, "note": note, "listed": picked is None or k in picked}
                    for k, name, note in sidebar.known_hosts()]
        return {
            "chats": (chats, lambda r: f"{r['id']} {json.dumps(r['title'])} · {r['folder']} · " +
                      (f"group {r['group']} · " if r["group"] else "") + f"{r['host']} · {r['ago']}" +
                      (f" · {r['state']}" if r["state"] not in (None, "inactive") else "") +
                      (" · archived" if r["archived"] else "")),
            "groups": (groups, lambda r: r["name"] + (" · listed" if r["listed"] else "")),
            "hosts": (hosts, lambda r: r["name"] + (f" ({r['note']})" if r["note"] else "") +
                      (" · listed" if r["listed"] else "")),
        }

    def context(self, agent, msg):
        """What's @-referenced: a chat's conversation, a file's text, or what it is."""
        said = []
        for item in self.referenced(msg["text"]):
            if item.get("kind") == "chat" and item.get("path"):
                said.append(f'<chat title="{meta.attr(item["surface"])}" id="{item.get("id", "")[:8]}">\n'
                            f'{meta.transcript_text(item["path"], 12000)}\n</chat>')
                agent.allowed.add(os.path.realpath(item["path"]))
            elif (path := item.get("path")) and os.path.isfile(path):
                try:
                    with open(path, encoding="utf-8", errors="replace") as f:
                        text = f.read(meta.REF_LIMIT + 1)
                except OSError:
                    text = "(not read)"
                said.append(f'<file name="{meta.attr(item["surface"])}" path="{meta.attr(path)}">\n'
                            f'{meta.trimmed(text, meta.REF_LIMIT, path)}\n</file>')
                agent.allowed.add(os.path.realpath(path))
            else:
                said.append(f'<ref name="{meta.attr(item["surface"])}">{meta.attr(item.get("what") or "")}, '
                            f'{meta.attr(item.get("summary") or "")}</ref>')
        return ["<referenced>\n" + "\n".join(said) + "\n</referenced>"] if said else []


class SidePanel(Gtk.Box):
    """Right-hand pane: Meta (BaudeMeta), subagents (each with its own transcript) and background processes
    (status, live output tail, stop)."""

    def __init__(self, win):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.add_css_class("side-panel")
        self.win, self.agent_rows, self.agent_boxes, self.proc_rows = win, {}, {}, {}
        self.stack = Adw.ViewStack(vexpand=True)  # without this the pages get 0px of height
        switcher = Adw.ViewSwitcher(stack=self.stack, policy=Adw.ViewSwitcherPolicy.NARROW,
                                    margin_top=6, margin_bottom=6, halign=Gtk.Align.CENTER)
        self.append(switcher)  # (no rule under it: the frame has none: widgets.CSS)
        self.append(self.stack)

        # Meta, Baude's helper: first, its spanner
        self.meta = BaudeMeta(win)
        page = self.stack.add_titled(self.meta, "meta", meta.NAME)
        page.set_icon_name("baude-meta-symbolic")

        # subagents: list -> detail
        self.agent_stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.SLIDE_LEFT_RIGHT, vexpand=True)
        self.agent_list = TreeList(line_x=8, selection_mode=Gtk.SelectionMode.NONE)
        self.agent_list.add_css_class("boxed-list")
        self.agent_list.set_placeholder(self._empty("No subagents yet"))
        self.agent_list.connect("row-activated", lambda _l, row: self.open_agent(row.tool_use_id))
        self.agent_stack.add_named(self._scrolled(self.agent_list), "list")
        detail = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        top = Gtk.Box(spacing=6, margin_start=8, margin_end=8, margin_top=6, margin_bottom=6)
        back = Gtk.Button(icon_name="go-previous-symbolic", tooltip_text="All subagents")
        back.add_css_class("flat")
        back.connect("clicked", lambda *_: self.agent_stack.set_visible_child_name("list"))
        self.detail_title = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, hexpand=True)
        self.detail_title.add_css_class("heading")
        top.append(back)
        top.append(self.detail_title)
        detail.append(top)
        self.detail_info = Gtk.Label(xalign=0, wrap=True, margin_start=14, margin_end=14)
        self.detail_info.add_css_class("dim-label")
        self.detail_info.add_css_class("caption")
        detail.append(self.detail_info)
        # its chat set into the panel as the main chat is into the window (the inside: widgets.inside), the back
        # button, title and caption above it on the panel's own background. No gap of its own on the main chat's
        # side: the main chat's is the one between them, as thin as the window's edges are
        self.detail_holder = widgets.inside(Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER,
                                                               margin_top=6), start=False)
        detail.append(self.detail_holder)
        self.agent_stack.add_named(detail, "detail")
        page = self.stack.add_titled(self.agent_stack, "agents", "Subagents")
        page.set_icon_name("system-users-symbolic")

        # background processes
        self.proc_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.proc_list.add_css_class("boxed-list")
        self.proc_list.set_placeholder(self._empty("No background processes"))
        page = self.stack.add_titled(self._scrolled(self.proc_list), "processes", "Processes")
        page.set_icon_name("utilities-terminal-symbolic")

        # artifacts published (or linked) in this session
        self.artifact_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.artifact_empty = self._empty("No artifacts yet")
        self.artifact_list.append(self.artifact_empty)
        self.artifact_rows = {}
        # /btw side questions, newest first
        self.btw_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.btw_empty = self._empty("Ask a side question with /btw <question>\n(it doesn't interrupt or join the chat)")
        self.btw_empty.set_justify(Gtk.Justification.CENTER)
        self.btw_list.append(self.btw_empty)
        page = self.stack.add_titled(self._scrolled(self.btw_list), "btw", "Side questions")
        page.set_icon_name("dialog-question-symbolic")
        page = self.stack.add_titled(self._scrolled(self.artifact_list), "artifacts", "Artifacts")
        page.set_icon_name(globe_icon())
        # (code shown in Baude is a tab of its own in the main area: BaudeWindow.show_code)
        # folders: a tree, opened down to the one clicked (not the file manager)
        self.files_page = FilesPage(win)
        page = self.stack.add_titled(self.files_page, "files", "Files")
        page.set_icon_name("folder-symbolic")
        # git: a branch's history, its upstream and parent, its commits' changes (see "git view")
        self.git_page = GitPage(win)
        page = self.stack.add_titled(self.git_page, "git", "Git")
        page.set_icon_name(themed_icon("vcs-branch-symbolic", "media-playlist-shuffle-symbolic"))
        self.stack.connect("notify::visible-child-name", lambda *_: (
            self.stack.get_visible_child_name() == "files" and self.files_page.ensure(),
            self.stack.get_visible_child_name() == "git" and self.git_page.ensure()))
        # Signal's names
        self.signal_page = SignalPage(win)
        page = self.stack.add_titled(self.signal_page, "refs", "Signal")
        page.set_icon_name(themed_icon("insert-link-symbolic", "emblem-symbolic-link-symbolic"))
        self.poll_source = GLib.timeout_add(1000, self.poll_output)

    @staticmethod
    def _empty(text):
        lbl = Gtk.Label(label=text, margin_top=24, margin_bottom=24)
        lbl.add_css_class("dim-label")
        return lbl

    @staticmethod
    def _scrolled(child):
        child.set_margin_start(10)
        child.set_margin_end(10)
        child.set_margin_top(10)
        child.set_margin_bottom(10)
        child.set_valign(Gtk.Align.START)
        return Gtk.ScrolledWindow(child=child, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)

    def show_page(self, name):
        self.stack.set_visible_child_name(name)

    def add_side_question(self, sq):
        self.btw_list.prepend(sq)
        self.side_questions_changed()

    def side_questions(self):
        return [w for w in walk(self.btw_list) if isinstance(w, SideQuestion)]

    def side_questions_changed(self):
        self.btw_empty.set_visible(not self.side_questions())

    def update_artifact(self, info):
        old = self.artifact_rows.pop(info["url"], None)
        chip = ArtifactChip(info)
        if old is not None:
            self.artifact_list.insert_child_after(chip, old)
            self.artifact_list.remove(old)
            discard(old)
        else:
            self.artifact_list.prepend(chip)
        self.artifact_rows[info["url"]] = chip
        self.artifact_empty.set_visible(False)

    def reset(self):
        for chip in self.artifact_rows.values():
            self.artifact_list.remove(chip)
            discard(chip)
        self.artifact_rows.clear()
        self.artifact_empty.set_visible(True)
        for lst in (self.agent_list, self.proc_list):
            while (row := lst.get_row_at_index(0)) is not None:
                lst.remove(row)
                discard(row)
        self.agent_rows.clear()
        if isinstance(shown := self.detail_holder.get_child(), Gtk.Viewport):
            shown.set_child(None)  # (the subagent shown: its transcript goes with the rest)
        for box in self.agent_boxes.values():
            discard(box)
        self.agent_boxes.clear()
        self.proc_rows.clear()
        self.agent_stack.set_visible_child_name("list")

    # ---- subagents
    def agent_view(self, tool_use_id):
        """The transcript box for a subagent's messages, or None if it isn't a tracked subagent."""
        return self.agent_boxes.get(tool_use_id)

    def link_card(self, card, task):
        """A subagent's tool card: View, beside its Prompt where its Output was, opens its transcript here (the side
        panel's Subagents, on it) rather than folding open what it said. Its Output's kept only for an Error."""
        if getattr(card, "view_btn", None) is None:
            tid = task.get("tool_use_id") or card.tool_id
            btn = Gtk.ToggleButton(label="View", valign=Gtk.Align.CENTER, focus_on_click=False,
                                   tooltip_text="Open this subagent in the side panel")
            btn.add_css_class("tool-toggle")  # (as Prompt is, beside it)
            btn.connect("toggled", lambda b: b.get_active() and (b.set_active(False), self.win.show_panel(True, "agents"),
                                                                self.open_agent(tid)))
            card.toggles.append(btn)
            card.view_btn = btn
        if card.state != "error":
            card.output_toggle.set_visible(False)
        card.toggles.set_visible(True)

    def update_task(self, task):
        if task.get("task_type") == "local_agent":
            self.update_agent(task)
        elif task.get("task_type"):
            self.update_process(task)

    @staticmethod
    def describe(task, marked=False):
        """A task's state and what it's done so far. marked: for a list row, whose spinner or ✓ already says running
        or completed, so only the other endings (failed, killed, interrupted) are said in words."""
        status = task.get("status", "running")
        usage = task.get("usage") or {}
        bits = [] if marked and status in ("running", "completed") else [status]
        if task.get("subagent_type"):
            bits.insert(0, task["subagent_type"])
        if usage.get("tool_uses"):
            bits.append(f"{usage['tool_uses']} tool use{'s' if usage['tool_uses'] != 1 else ''}")
        if usage.get("total_tokens"):
            bits.append(f"{fmt_tokens(usage['total_tokens'])} tokens")
        if usage.get("duration_ms"):
            bits.append(f"{usage['duration_ms'] / 1000:.1f}s")
        if status == "running" and task.get("activity"):
            bits.append(task["activity"])
        return " · ".join(bits)

    def update_agent(self, task):
        tid = task.get("tool_use_id") or task["task_id"]
        row = self.agent_rows.get(tid)
        if row is None:
            row = Adw.ActionRow(activatable=True)
            row.tool_use_id = tid
            row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
            # an agent started by another agent sits under it, indented like a file in a folder
            parent = self.agent_rows.get(getattr(self.win, "tool_parent", {}).get(tid))
            row.depth = parent.depth + 1 if parent is not None else 0
            row.status = StatusSlot()
            row.add_prefix(row.status)
            row.tree_parent, row.tree_anchor = parent, row.status
            if row.depth:  # indented; the list draws the line to it (a later prefix goes further left)
                row.add_prefix(Gtk.Box(width_request=row.depth * TreeList.INDENT - 6))
                row.add_css_class("nested-agent")
            self.agent_rows[tid] = row
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_start=12, margin_end=12,
                          margin_top=12, margin_bottom=12)  # (in its cutout, as far in from the top as the chat's)
            box.add_css_class("zoomable")
            if task.get("prompt"):
                box.append(self.win.user_bubble(task["prompt"], []))
            self.agent_boxes[tid] = box
            if parent is not None:  # right under its parent (newest first, like the top level)
                self.agent_list.insert(row, parent.get_index() + 1)
            else:
                self.agent_list.prepend(row)
        row.set_title(html.escape(task.get("description") or "Subagent"))
        row.set_subtitle(html.escape(self.describe(task, marked=True)))
        row.status.set_state({"running": "running", "completed": "done",
                              "interrupted": "interrupted"}.get(task.get("status"), "error"))
        if self.agent_stack.get_visible_child_name() == "detail" and self.current == tid:
            self.detail_info.set_text(self.describe(task))

    current = None

    def open_agent(self, tool_use_id):
        box = self.agent_boxes.get(tool_use_id)
        if box is None:
            return
        self.current = tool_use_id
        task = next((t for t in self.win.tasks.values() if t.get("tool_use_id") == tool_use_id), {})
        self.detail_title.set_text(task.get("description") or "Subagent")
        self.detail_info.set_text(self.describe(task))
        if isinstance(old := self.detail_holder.get_child(), Gtk.Viewport):
            old.set_child(None)  # release the previous subagent's box so it can be shown again
        self.detail_holder.set_child(box)
        self.agent_stack.set_visible_child_name("detail")
        self.show_page("agents")

    # ---- background processes
    def update_process(self, task):
        tid = task["task_id"]
        row = self.proc_rows.get(tid)
        if row is None:
            row = Adw.ExpanderRow(title_lines=1, subtitle_lines=1)  # one line each; all of it below
            row.task_id = tid
            row.status = StatusSlot()
            row.add_prefix(row.status)
            row.stop = Gtk.Button(icon_name="media-playback-stop-symbolic", tooltip_text="Stop this process",
                                  valign=Gtk.Align.CENTER)
            row.stop.add_css_class("flat")
            row.stop.connect("clicked", lambda *_: self.win.stop_task(tid))
            row.add_suffix(row.stop)
            card = self.win.tool_cards.get(task.get("tool_use_id"))
            cmd = card.input.get("command") if card is not None else None
            body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                           margin_start=12, margin_end=12, margin_top=10, margin_bottom=12)
            if cmd:
                body.append(CodeBlock(cmd, "bash"))
            row.output = CodeBlock("(no output yet)", "output")
            body.append(row.output)
            row.add_row(body)
            self.proc_rows[tid] = row
            self.proc_list.prepend(row)
        card = self.win.tool_cards.get(task.get("tool_use_id"))
        cmd = (card.input.get("command") if card is not None else None) or ""
        said = (card.input.get("description") if card is not None else None) or ""
        desc = task.get("description") or ""
        if said.strip() or (desc.strip() and desc.strip() != cmd.strip() and not cmd):
            row.set_title(html.escape(said or desc))  # Claude's words for it
        else:  # no description: the command itself, as code (never our own summary of it)
            first = (cmd or desc or tid).strip().splitlines()[0] if (cmd or desc or tid).strip() else tid
            row.set_title(f"<tt>{html.escape(first[:300])}</tt>")
        row.set_tooltip_text(cmd or desc or None)
        row.set_subtitle(html.escape(self.describe(task, marked=True)))
        running = task.get("status", "running") == "running"
        row.stop.set_visible(running)
        row.status.set_state("running" if running else {"completed": "done", "interrupted": "interrupted"}
                             .get(task.get("status"), "error"))
        self.refresh_output(row, task)

    def refresh_output(self, row, task):
        text = read_tail(task.get("output_file"), 4000)
        if text and text != row.output.code:
            row.output.code = text
            row.output.label.set_text(text)

    def poll_output(self):
        """Live output for running processes while the panel is open."""
        if self.get_visible():
            for tid, row in self.proc_rows.items():
                task = self.win.tasks.get(tid, {})
                if task.get("status", "running") == "running":
                    self.refresh_output(row, task)
        return True


# ------------------------------------------------------- signal views --

def ref_location_text(loc):
    where = f"{loc['path']}:{loc['line']}" if loc.get("line") else loc["path"]
    return f"{loc['role']} · {where}" + (f"  ({loc['note']})" if loc.get("note") else "")


_CHECKOUTS = {}


def git_checkout(path):
    """(the checkout's top folder, its repository's common git folder) for a folder in a git checkout, else None.
    A worktree has its own top folder and its main checkout's common folder."""
    if path not in _CHECKOUTS:
        out = _git(path, "rev-parse", "--path-format=absolute", "--show-toplevel", "--git-common-dir") or ""
        parts = out.split("\n")
        _CHECKOUTS[path] = (os.path.realpath(parts[0]), os.path.realpath(parts[1])) if len(parts) >= 2 and parts[1] \
            else None
    return _CHECKOUTS[path]


_FOLDERS = {}
_REF_WHERE = re.compile(rb'"(cwd|file_path|notebook_path|command|name)":[ \t\r\f\v]*"((?:[^"\\\n]|\\.)*)"')  # (in a line)
_REF_CD = re.compile(r"(?:^|&&|;|\|\||\n|\(|\{|\bthen\b|\bdo\b)\s*(?:cd|pushd)\s+(\"[^\"]+\"|'[^']+'|(?:\\ |[^\s;&|()])+)")
_REF_GIT_C = re.compile(r"\bgit\s+-C\s+(\"[^\"]+\"|'[^']+'|(?:\\ |\S)+)\s+(?:commit|push|pull|merge|rebase|checkout|switch|"
                        r"cherry-pick|reset|revert|add|rm|mv|stash|tag|am|apply|restore)\b")
_REF_WRITERS = ("Edit", "Write", "MultiEdit", "NotebookEdit")


def transcript_folders(path):
    """Where a chat worked, the most recent first, each folder once: (folder, acted). The folder each message ran in
    (acted False: prompts are recorded there whatever the chat does), and, acted, the folders its commands cd into or
    change with git -C (cd ../wt && ..., git -C ../wt commit), and the folders of the files it edits or writes. Reading
    a file somewhere isn't working there. A worktree beside the repository is outside the project, so Claude Code puts
    the shell back after each command there, and only the commands and edits say the chat worked in it. Read from the
    transcript's end back, only as far as they're asked for: chat_checkout wants the latest that says something, mostly
    in the last few hundred KB (read whole, a 50 to 85 MB chat took 0.3 to 1.5 s of opening it). Kept per transcript, and
    as it grows only what it grew by is read (this runs for each line another program appends)."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            trail = _folder_trail(path, f, size)
            i = 0
            while True:
                while i < len(trail["keys"]):
                    yield trail["keys"][i]
                    i += 1
                if not _trail_take(trail, f):
                    return
    except OSError:
        return


def _folder_events(data, cwd=..., writing=...):
    """What transcript_folders reads in whole transcript lines (bytes), in order, from the state the lines before them
    leave: cwd, the folder the last message ran in (None before any); writing, whether the call whose input comes next
    writes files; `...` for either where those lines aren't read yet. ([(folder, acted)], cwd, writing): the state they
    leave, `...` still where they don't say. One that hangs on a state not read is (target, "cd") (a cd into a relative
    folder, before their first message's folder) or (folder, "write") (a file's, before their first call: written if the
    call before them writes). Folders as found: _trail_folders normalizes them, and drops relative ones."""
    events = []
    for m in _REF_WHERE.finditer(data):
        try:
            value = json.loads('"' + m[2].decode("utf-8", "replace") + '"')
        except ValueError:
            continue
        if m[1] == b"name":
            writing = value in _REF_WRITERS  # (the tool whose input follows)
        elif m[1] == b"cwd":
            cwd = value
            events.append((value, False))
        elif m[1] == b"command":
            for t in _REF_CD.findall(value) + _REF_GIT_C.findall(value):
                d = os.path.expanduser(t.strip("\"'").replace("\\ ", " "))
                if os.path.isabs(d):
                    events.append((d, True))
                elif cwd is ...:
                    events.append((d, "cd"))
                elif cwd:
                    events.append((os.path.join(cwd, d), True))
        elif writing is not False and os.path.isabs(value):
            events.append((os.path.dirname(value), True if writing is True else "write"))
            writing = False
    return events, cwd, writing


def _folder_trail(path, f, size):
    """transcript_folders' memory of a transcript: "keys", its folders found so far, newest first; "seen", them as a set;
    "blocks", what's read of it, newest first, each [start, events (_folder_events; None once in keys), cwd, writing];
    the first "taken" of them in keys. The lines added since it was last looked at are read now (a line still being
    written isn't), and their folders go first; one rewritten (shorter, or its end not what was read) is read again."""
    trail = _FOLDERS.get(path)
    if trail is not None and trail["size"] == size:
        return trail
    end, at, step = 0, size, 1 << 16
    while at > 0:  # (the whole lines end at the last newline)
        start = max(0, at - step)
        f.seek(start)
        if (nl := f.read(at - start).rfind(b"\n")) >= 0:
            end = start + nl + 1
            break
        at, step = start, step * 2
    if trail is not None:
        f.seek(trail["last"][0])
        if end < trail["end"] or hash(f.read(trail["end"] - trail["last"][0])) != trail["last"][1]:
            trail = None
    if trail is None:
        trail = _FOLDERS[path] = {"end": end, "keys": [], "seen": set(), "blocks": [], "taken": 0}
    elif end > trail["end"]:
        f.seek(trail["end"])
        trail["blocks"].insert(0, [trail["end"], *_folder_events(f.read(end - trail["end"]))])
        trail["taken"] += 1
        fresh = _trail_folders(trail, f, 0)
        trail["seen"].update(fresh)
        fresh_set = set(fresh)
        trail["keys"] = fresh + [k for k in trail["keys"] if k not in fresh_set]
    back = max(0, end - 4096)
    f.seek(back)
    trail.update(size=size, end=end, last=(back, hash(f.read(end - back))))  # (to tell it's the file read, next time)
    return trail


def _trail_read(trail, f):
    """The lines of a trail's transcript before the earliest read, parsed: 256 KB of them, twice as many each time
    after, to 8 MB (a line longer than that, all of it). False at its start."""
    blocks = trail["blocks"]
    at = blocks[-1][0] if blocks else trail["end"]
    size = (1 << 18) << min(len(blocks), 5)
    while at > 0:
        start = max(0, at - size)
        f.seek(start)
        data = f.read(at - start)
        nl = data.find(b"\n") if start else -1  # (the line it begins in: part of the block before)
        if start and not 0 <= nl < len(data) - 1:
            size *= 2
            continue
        blocks.append([start + nl + 1, *_folder_events(data[nl + 1:])])
        return True
    return False


def _trail_state(trail, f, i):
    """(cwd, writing) where a trail's block i begins: as the blocks before it leave them, read back as far as it takes."""
    blocks, cwd, writing = trail["blocks"], ..., ...
    while cwd is ... or writing is ...:
        if blocks[i][0] == 0:  # (the transcript's start: none yet)
            return None if cwd is ... else cwd, False if writing is ... else writing
        i += 1
        if i == len(blocks):
            _trail_read(trail, f)
        cwd = blocks[i][2] if cwd is ... else cwd
        writing = blocks[i][3] if writing is ... else writing
    return cwd, writing


def _trail_folders(trail, f, i):
    """A trail's block i as its folders, newest first, each once: what hangs on the lines before it, as they say."""
    events, trail["blocks"][i][1] = trail["blocks"][i][1], None
    if any(acted in ("cd", "write") for _d, acted in events):
        cwd, writing = _trail_state(trail, f, i)
    out, seen = [], set()
    for d, acted in reversed(events):
        if acted == "cd":
            d, acted = os.path.join(cwd, d) if cwd else None, True
        elif acted == "write":
            if not writing:
                continue
            acted = True
        if d and os.path.isabs(d):  # (a folder acted in and one only run in are two entries, each where it was last)
            key = (os.path.normpath(d), acted)
            if key not in seen:
                seen.add(key)
                out.append(key)
    return out


def _trail_take(trail, f):
    """The next block back of a trail's transcript into its keys (read now if it isn't yet). False at its start."""
    if trail["taken"] == len(trail["blocks"]) and not _trail_read(trail, f):
        return False
    for key in _trail_folders(trail, f, trail["taken"]):
        if key not in trail["seen"]:
            trail["seen"].add(key)
            trail["keys"].append(key)
    trail["taken"] += 1
    return True


_TREES = {}


def git_trees(common):
    """A repository's checkouts (its main one and its worktrees), by its common git folder: their top folders, read
    again when a worktree is added or removed (its worktrees folder changes), or after 30 s."""
    try:
        stamp = os.stat(os.path.join(common, "worktrees")).st_mtime_ns
    except OSError:
        stamp = 0
    hit = _TREES.get(common)
    if hit is None or hit[1] != stamp or time.time() - hit[0] > 30:
        out = _git(os.path.dirname(common) if os.path.basename(common) == ".git" else common,
                   "worktree", "list", "--porcelain") or ""
        hit = _TREES[common] = (time.time(), stamp, [os.path.realpath(l[9:]) for l in out.splitlines()
                                                     if l.startswith("worktree ")])
    return hit[2]


def chat_checkout(folder, transcript):
    """The checkout a chat works in, when it isn't its folder's: the latest other checkout of the same repository
    (a worktree) it worked in (transcript_folders: where its messages ran, its commands went, the files it wrote), if
    it's still there. Else None. Its own folder counts against it only where the chat acted there (a cd, an edit):
    prompts are recorded there, and the shell goes back there between commands."""
    here = git_checkout(os.path.realpath(folder)) if transcript else None
    if here is None:
        return None
    trees = sorted(git_trees(here[1]), key=len, reverse=True)  # (the deepest first: .claude/worktrees/x in its repo)
    for d, acted in transcript_folders(transcript):  # (the latest first)
        real = os.path.realpath(d)
        tree = next((t for t in trees if real == t or real.startswith(t + os.sep)), None)
        if tree == here[0] and acted:
            return None  # (it worked in its own checkout since)
        if tree is not None and tree != here[0] and os.path.isdir(tree):
            return tree
    return None


_REF_TABLE_OF = re.compile(r"""(?:\bTable\(|create_table\(|add_column\(|alter_column\(|drop_column\(|__tablename__\s*=)\s*["'](\w+)["']"""
                           r"""|\b(?:CREATE|ALTER)\s+TABLE\s+(?:IF\s+(?:NOT\s+)?EXISTS\s+)?["`]?(?:\w+\.)?(\w+)""", re.I)


def ref_column_table(path, line, back=300):
    """The table a column's definition belongs to: the nearest sa.Table("t", / create_table("t", /
    add_column("t", / __tablename__ = "t" / CREATE or ALTER TABLE t at or above its line. None if none."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.read(4 * 1024 * 1024).splitlines()
    except OSError:
        return None
    for i in range(min(len(lines), line or 1) - 1, max(-1, (line or 1) - 1 - back), -1):
        if m := _REF_TABLE_OF.search(lines[i]):
            return m.group(1) or m.group(2)
        if re.search(r"\b(?:Table|create_table|add_column|alter_column|drop_column)\(\s*$", lines[i]) and \
                i + 1 < len(lines) and (m := re.match(r"""\s*["'](\w+)["']""", lines[i + 1])):
            return m.group(1)  # (sa.Table(\n    "svc_job",)
    return None


def ref_busy(ref):
    """Whether Signal is working: a lookup, replies waiting, or Sonnet with a batch or replies to come."""
    return bool(ref.activity or ref.waiting or (ref.agent is not None and ref.agent.busy()))


def ref_late(ref):
    """How long after its message a name was looked up, as its status says it; None if that's not worth saying."""
    return time_ago(ref.written_at).replace(" ago", "") if ref.after_fact and ref.written_at else None


def ref_status_tip(label, ref):
    """A status line (ref_status_text): when the message was written, with the pointer on how long after."""
    late = ref_late(ref)
    return time_tip(label, ref.written_at if late else None, late)


def ref_status_text(ref):
    """The status line of a popover or card."""
    if ref.status == "pending":
        return "Looking it up…"
    if ref.status == "not_found":
        return f"Not found: {ref.reason}."
    if ref.status == "stale":
        return "Stale: the code changed and this couldn't be found again. It can still be opened as it was."
    if ref.status == "ambiguous":
        return f"{len(ref.locations)} matches ({ref.reason})."
    note = ""
    if late := ref_late(ref):
        note = f" Looked up {late} after the message was written, so the line is the one there now."
    return ref.summary() + "." * (not ref.summary().endswith((".", ")"))) + note


def ref_location_buttons(win, ref, box, limit=None, on_open=None):
    """A row per location: click to open it where it is now; a stale one can also be opened as it was."""
    locs = ref.locations if limit is None else ref.locations[:limit]
    for i, loc in enumerate(locs):
        row = Gtk.Box(spacing=4)
        btn = Gtk.Button(hexpand=True, tooltip_text=os.path.join(win.signal.root, loc["path"]))
        btn.add_css_class("flat")
        btn.add_css_class("ref-loc")
        lbl = Gtk.Label(label=ref_location_text(loc), xalign=0, ellipsize=Pango.EllipsizeMode.MIDDLE)
        btn.set_child(lbl)
        btn.connect("clicked", lambda *_, i=i: win.open_location(ref, i) and on_open and on_open())
        row.append(btn)
        if ref.status == "stale" and loc.get("blob"):
            was = Gtk.Button(label="As it was", tooltip_text="Open the version that was read when it was looked up")
            was.add_css_class("flat")
            was.connect("clicked", lambda *_, i=i: (win.open_as_it_was(ref, i), on_open and on_open()))
            row.append(was)
        box.append(row)
    return len(ref.locations) - len(locs)


def ref_run_names(ref):
    """The names a link's thing could be running under in a cluster: a service's, a workload's (a Deployment the
    charts define), a folder's (services/web-svc: web-svc). Only names Kubernetes allows."""
    if ref.status not in ("resolved", "ambiguous") or ref.data.get("context") or ref.data.get("db"):
        return []
    if ref.kind == "k8s" and ref.data.get("kind") not in KUBE_WORKLOADS + ("Service",):
        return []
    names = [ref.data.get("name"), ref.surface, ref.canonical] if ref.kind in ("k8s", "service") else []
    names += [os.path.basename(l["path"].rstrip("/")) for l in ref.locations if l["role"] == "folder"]
    return list(dict.fromkeys(n for n in names if n and re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", n)))


def kube_running_rows(win, names, box, on_open=None):
    """Where a link's thing runs in your local clusters (kube_running, asked in the background), added to box once
    it's known; nothing if it runs nowhere. A row per workload, which opens it in the Kubernetes view."""
    holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, visible=False)
    box.append(holder)

    def show(found):
        if not found or holder.get_root() is None:
            return False
        heading = Gtk.Label(label="Running", xalign=0)
        heading.add_css_class("ref-kind")
        holder.append(heading)
        for r in found:
            btn = Gtk.Button(tooltip_text=f"Open it in the Kubernetes view: {r['context']}, namespace {r['namespace']}")
            btn.add_css_class("flat")
            btn.add_css_class("ref-loc")
            line = Gtk.Box(spacing=6)
            dot = Gtk.Label(label="●")
            dot.add_css_class("success" if r["ok"] else "warning")
            line.append(dot)
            line.append(time_tip(Gtk.Label(label=f"{r['kind']} {r['name']} · {r['context']} · {r['namespace']} · {r['state']}",
                                           xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.MIDDLE),
                                 r.get("at"), r["state"]))
            btn.set_child(line)
            btn.connect("clicked", lambda *_, r=r: (on_open and on_open(), win.kube.open(
                context=r["context"], page=kube_page_for(r["resource"]), search=r["name"], namespace=r["namespace"])))
            holder.append(btn)
        holder.set_visible(True)
        if isinstance(pop := holder.get_ancestor(Gtk.Popover), AnchoredPopover):
            pop.refit()  # (taller now, after it was shown)
        return False

    def work():
        try:
            found = kube_running(names)
        except Exception:  # noqa: BLE001 (kubectl gone: nothing to say)
            found = []
        GLib.idle_add(show, found)
    KUBE_POOL.submit(work)
    return holder


def ref_known_text(win, surface):
    """What else a name is, when the name catalogue has it from more than one kind of source (a branch and a table
    both called it, say): "Known as: Branch · Database table (relay, postgres)". None otherwise."""
    sources = win.signal.known_from(surface) if getattr(win, "signal", None) is not None else []
    if len({what for what, _where in sources}) < 2:
        return None
    return "Known as: " + " · ".join(what + (f" ({where})" if where else "") for what, where in sources)


def ref_kind_label(ref):
    """What a name is, as a popover's title says it: nothing while it's being looked up (it may be anything),
    a database table or column when that's what the code defines, else its kind."""
    if ref.status == "pending":
        return None
    if (live := ref.data.get("db")):  # (a running database's: no code defines it)
        if not live.get("table"):
            return "Database"
        return "Database collection" if live.get("engine") == "ArangoDB" else "Database table"
    roles = [l["role"] for l in ref.locations if l["role"] != "used"]
    if ref.kind == "symbol" and roles and all(r in ("table", "column") for r in roles):
        return "Database column" if all(r == "column" for r in roles) else "Database table"
    if ref.data.get("context"):
        return "Kubeconfig cluster" if ref.data.get("cluster_only") else "Kubeconfig context"
    if ref.data.get("issue"):  # (GitHub's list says so: a # in a reply may be either)
        return "Issue"
    return REF_KINDS.get(ref.kind, ("Name",))[0]


def name_markup(name):
    """A title's name: <tt>name</tt>."""
    return f"<tt>{html.escape(name)}</tt>"


class AnchoredPopover(Gtk.Popover):
    """A popover pointing into a reply's label; it goes when the label does (a new session)."""

    def __init__(self):
        super().__init__()
        self.add_css_class("ref-popover")
        self.anchor_handler, self.is_closed = None, False
        self.connect("closed", self._on_closed)

    def _on_closed(self, *_):
        self.is_closed = True
        GLib.idle_add(self.detach)

    def attach(self, anchor, rect=None):
        """Point at rect in anchor (a reply's label); leave with it if it goes (a new session)."""
        self.set_parent(anchor)
        if rect is not None:
            self.set_pointing_to(rect)
        self.anchor_handler = anchor.connect("unrealize", lambda *_: self.detach())

    def detach(self):
        anchor = self.get_parent()
        if anchor is not None:
            if self.anchor_handler:
                anchor.disconnect(self.anchor_handler)
                self.anchor_handler = None
            self.unparent()
        return False

    def refit(self):
        """What it shows changed while it's open: shown again at its new size. GTK closes a popover whose content
        outgrows the size it was shown at unless the widget holding it presents it again as it's allocated, which a
        label (what these point into) only does for its own menu."""
        if self.get_visible():
            self.present()


class LookupPopover(AnchoredPopover):
    """A click on a name whose place isn't known yet: what's happening ("Looking up…", "Finding it in the running
    databases…"), then what was found, with buttons to open it, pick one of several, or try again."""

    def __init__(self, name, kind=None):
        super().__init__()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=4, margin_bottom=4,
                      margin_start=4, margin_end=4, width_request=320)
        self.title = Gtk.Label(xalign=0, wrap=True, max_width_chars=60, wrap_mode=Pango.WrapMode.WORD_CHAR)
        box.append(self.title)
        line = Gtk.Box(spacing=6)
        self.spinner = Spinner(visible=False)
        line.append(self.spinner)
        self.status = Gtk.Label(xalign=0, wrap=True, max_width_chars=60, hexpand=True)
        self.status.add_css_class("dim-label")
        line.append(self.status)
        box.append(line)
        self.choices = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.append(self.choices)
        self.actions = Gtk.Box(spacing=6, halign=Gtk.Align.END)
        box.append(self.actions)
        self.set_child(box)
        self.name = name
        self.set_title(kind)

    def set_title(self, kind=None):
        self.title.set_markup((f'<span alpha="65%">{html.escape(kind)}</span>  ' if kind else "") +
                              name_markup(self.name))

    def state(self, text, busy=False, choices=(), actions=()):
        """Say text (with a spinner while busy); choices: [(label, tooltip, fn)] as a list of places, actions:
        [(label, fn)] as buttons, the first the default."""
        self.status.set_label(text)
        self.spinner.set_visible(busy)
        for box in (self.choices, self.actions):
            while (c := box.get_first_child()) is not None:
                box.remove(c)
                discard(c)
        for label, tip, fn in choices:
            btn = Gtk.Button(tooltip_text=tip or None)
            btn.add_css_class("flat")
            btn.add_css_class("ref-loc")
            btn.set_child(Gtk.Label(label=label, xalign=0, ellipsize=Pango.EllipsizeMode.MIDDLE))
            btn.connect("clicked", lambda *_, fn=fn: (self.popdown(), fn()))
            self.choices.append(btn)
        for i, (label, fn) in enumerate(actions):
            btn = Gtk.Button(label=label)
            if i == 0:
                btn.add_css_class("suggested-action")
            btn.connect("clicked", lambda *_, fn=fn: fn())
            self.actions.append(btn)
        if (first := self.actions.get_first_child()) is not None:
            self.set_default_widget(first)
        self.refit()  # (choices and buttons where there was a spinner: bigger)


def ref_palette_items(signal):
    """What the glossary's search lists (lookup.items: the names Signal looked up, and the catalogue's), each with
    open(win): a looked-up name as its link opens, a catalogued one where it is (ref_palette_open)."""
    items = lookup.items(signal)
    for it in items:
        if it["entry"] is None:
            it["open"] = lambda win, s=it["surface"]: win.open_ref(s, win.title_label)
        else:
            it["open"] = lambda win, n=it["surface"], e=it["entry"], root=signal.root: ref_palette_open(win, n, e, root)
    return items


def ref_palette_open(win, name, entry, root):
    """Open a catalogued name (one no reply has looked up): a running workload or a context in the Kubernetes view,
    filtered to it; a manifest's object or a file at its file; a branch in the Git page; a table in its database's
    diagram; a pull request or an issue on GitHub (or on the Feedback tab, one of Baude's own: fixview.open_github)."""
    if (listed := entry.get("github")) is not None:
        return fixview.open_github(win, listed)
    sources = entry.get("sources") or []
    if entry["kind"] == "k8s":
        running = next((w for what, w in sources if what.endswith(" running")), None)
        if running is not None:
            context, _, namespace = running.partition(" · ")
            return win.kube.open(context=context, search=name, namespace=namespace or None)
        if entry.get("hint") == "context":
            made = next((w for what, w in sources if w), None)  # (a cluster a tool made: its context)
            return win.kube.open(context=made or name)
        path = next((w for _what, w in sources if w), None)
        if path:
            return open_code(os.path.join(root, path), None, win)
    elif entry["kind"] == "branch":
        return win.show_git(rev=name)
    elif entry["kind"] == "file":
        path = sources[0][1] if sources else ""
        if path and not re.fullmatch(r"\d+ files", path):
            return open_code(os.path.join(root, path), None, win)
        return win.ref_toast(f"{path or 'Several files'} are called {name}", show=True)
    elif sources and sources[0][0] == "Database table":
        return win.open_db_name(name, None, "", None, win.title_label, name)
    elif sources and sources[0][0] == "Database":
        return win.schema.open()


class SearchSpace(lookup.SearchSpace):
    """⌘K's search (lookup.SearchSpace) of a Signal, its items opened in this window (ref_palette_items)."""

    def make_items(self):
        return ref_palette_items(self.source)


class RefPalette(widgets.RefPalette):
    """⌘K in Baude: the glossary's search of its chat's Signal (SearchSpace), a name opened as its link is (each item's
    open, ref_palette_items)."""

    def __init__(self, win):
        super().__init__(win, SearchSpace(win.catalogue_signal()), lambda item: item["open"](win))


class RefPopover(AnchoredPopover):
    """What a Signal link points at: its places, as ⌘K's rows (click one to open it), why it wasn't found, and at
    the top right, icons to search again and to open its card (and its cluster, or its schema)."""

    ROWS = 12  # places shown before the table scrolls

    def __init__(self, win, ref):
        super().__init__()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=4, margin_bottom=4,
                      margin_start=4, margin_end=4, width_request=520 if ref.locations else 320)
        head = Gtk.Box(spacing=2)
        title = Gtk.Label(xalign=0, wrap=True, max_width_chars=60, wrap_mode=Pango.WrapMode.WORD_CHAR, hexpand=True)
        kind = ref_kind_label(ref)
        title.set_markup((f'<span alpha="65%">{html.escape(kind)}</span>  ' if kind else "") +
                         name_markup(ref.canonical))  # (not selectable: it would open selected)
        self.title = title
        head.append(title)

        def icon_button(child, tip, fn):
            btn = Gtk.Button(child=child, tooltip_text=tip, valign=Gtk.Align.START)
            btn.add_css_class("flat")
            btn.connect("clicked", lambda *_: fn())
            head.append(btn)
            return btn
        if ref.kind == "k8s" and ref.data.get("context") and not ref.data.get("cluster_only"):
            icon_button(RefIcon("k8s"), "Open this cluster in the Kubernetes view",
                        lambda: (self.popdown(), win.kube.open(context=ref.data["context"])))
        if any(l["role"] in ("table", "column") for l in ref.locations):
            icon_button(RefIcon("db"), "Show it in its database's schema diagram",
                        lambda: (win.show_schema_for(ref, self.get_parent()), self.popdown()))
        icon_button(Gtk.Image.new_from_icon_name("view-refresh-symbolic"),
                    "Try again" if ref.status in ("not_found", "stale") else "Search again",
                    lambda: (win.signal.refresh([ref.surface], win.touched_paths()), self.popdown()))
        icon_button(Gtk.Image.new_from_icon_name("baude-open-symbolic"), "Open",
                    lambda: (self.popdown(), win.show_ref(ref.surface)))
        box.append(head)
        status = ref_status_tip(Gtk.Label(label=ref_status_text(ref), xalign=0, wrap=True, max_width_chars=60), ref)
        status.add_css_class("dim-label" if ref.status != "stale" else "warning")
        box.append(status)
        if known := ref_known_text(win, ref.surface):
            box.append(Gtk.Label(label=known, xalign=0, wrap=True, max_width_chars=60, css_classes=["dim-label"]))
        if ref.locations:
            self.places = Gio.ListStore(item_type=RefPaletteRow)
            self.places.splice(0, 0, [RefPaletteRow(it) for it in ref_place_items(win, ref, self.popdown)])
            self.table = RefPalette.make_table(Gtk.NoSelection(model=self.places), (200, 100, 220),
                                               lambda pos: self.places.get_item(pos).item["open"](win))
            box.append(Gtk.ScrolledWindow(child=self.table, hscrollbar_policy=Gtk.PolicyType.NEVER,
                                          propagate_natural_height=True, max_content_height=self.ROWS * 28))
        if names := ref_run_names(ref):  # (and where it runs, in your local clusters)
            self.running = kube_running_rows(win, names, box, self.popdown)
        self.set_child(box)


def ref_place_items(win, ref, on_open=None):
    """A Signal link's places as ⌘K's rows (RefPaletteRow items): the file (and line) with its logo, what's there
    (its role), the folder it's in; opened where it is now. A stale link's places have a row after each for the
    version read when it was looked up (As it was)."""
    items = []
    for i, loc in enumerate(ref.locations):
        path = loc["path"].rstrip("/")
        name = (os.path.basename(path) or path) + ("/" if loc["role"] == "folder" else "") + \
            (f":{loc['line']}" if loc.get("line") else "")
        where = os.path.dirname(path) + (f"  ({loc['note']})" if loc.get("note") else "")
        items.append({"surface": name, "canonical": "", "faint": False, "what": loc["role"].capitalize(),
                      "summary": where, "icon": ref_icon(ref.kind, ref.data, [loc["role"]]) or "code",
                      "open": lambda win, i=i: win.open_location(ref, i) and on_open and on_open()})
        if ref.status == "stale" and loc.get("blob"):
            items.append({**items[-1], "what": "As it was", "faint": True,
                          "open": lambda win, i=i: (win.open_as_it_was(ref, i), on_open and on_open())})
    return items


class SignalPage(Gtk.Stack):
    """The side panel's Signal page: the names this chat's replies mention, by kind, with how far
    each lookup got; a name opens its card (locations, a commit's PRs, ...)."""

    ICONS = {"resolved": ("object-select-symbolic", "success"), "ambiguous": ("view-more-symbolic", None),
             "pending": ("content-loading-symbolic", "dim-label"), "not_found": ("dialog-question-symbolic", "dim-label"),
             "stale": ("dialog-warning-symbolic", "warning")}

    def __init__(self, win):
        super().__init__(transition_type=Gtk.StackTransitionType.SLIDE_LEFT_RIGHT, vexpand=True)
        self.win, self.current, self.soon, self.status_soon = win, None, None, None
        top = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        bar = Gtk.Box(spacing=6)
        self.search = Gtk.SearchEntry(placeholder_text="Filter names", hexpand=True)
        self.search.connect("search-changed", lambda *_: self.rebuild())
        bar.append(self.search)
        self.refresh_btn = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Search again for the names not found")
        self.refresh_btn.connect("clicked", lambda *_: self.refresh_not_found())
        bar.append(self.refresh_btn)
        top.append(bar)
        # what it may use: toggle buttons (pressed: on)
        options = Gtk.Box(spacing=6)
        lsp = load_settings().get("signal_lsp")  # (unanswered, servers already installed are used: nothing to fetch)
        for attr, label, active, tip, fn in (
                ("sonnet", (load_settings().get("signal_model") or "sonnet").capitalize(),
                 bool(load_settings().get("signal_reader", True)),
                 "The reader: a model session reads the chat in order, like a person following along (your "
                 "messages, what Claude ran and saw, its replies), and decides what each name points at in each "
                 "reply, with Signal's lookups as its tools; its spending is capped and shown in the status line",
                 self.set_sonnet),
                ("lsp", "LSP", lsp is True or (lsp is None and bool(lsp_commands())),
                 "Language servers: pyright and typescript-language-server say which definition a code name means "
                 "(whose field previous.job_id is). Baude fetches them once with npm, at pinned versions, and runs "
                 "them read-only", self.set_lsp),
                ("tools", "Tools", bool(load_settings().get("signal_tool_schemas")),
                 "Tool schemas: when a reply talks about a tool (the Agent tool, Read), show what it takes, from "
                 "Anthropic's Agent SDK types and the Claude Code docs (fetched once per Claude Code version)",
                 self.set_tool_schemas)):
            btn = Gtk.ToggleButton(label=label, active=active, tooltip_text=tip)
            btn.add_css_class("ref-option")
            btn.connect("toggled", lambda b, fn=fn: fn(b.get_active()))
            options.append(btn)
            setattr(self, attr, btn)
        # Signal for this chat, on or off: the header's Activate Signal switch (off, the reader, the language servers and
        # the lookups waiting stop at once; its links stay)
        self.master = Gtk.Switch(hexpand=True, halign=Gtk.Align.END, valign=Gtk.Align.CENTER,
                                 tooltip_text="Signal, for this chat")
        if getattr(win, "signal_run", None) is not None:
            win.signal_run.bind_property("active", self.master, "active",
                                         GObject.BindingFlags.BIDIRECTIONAL | GObject.BindingFlags.SYNC_CREATE)
        options.append(self.master)  # (in line with what it may use)
        top.append(options)
        self.summary = Gtk.Label(xalign=0, wrap=True)
        self.summary.add_css_class("dim-label")
        self.summary.add_css_class("caption")
        top.append(self.summary)
        # what it's doing: now, and a log of every lookup (newest first)
        now = Gtk.Box(spacing=8)
        self.spinner = Spinner(visible=False)
        now.append(self.spinner)
        self.now = Gtk.Label(xalign=0, wrap=True, hexpand=True, wrap_mode=Pango.WrapMode.WORD_CHAR)
        self.now.add_css_class("ref-now")
        now.append(self.now)
        top.append(now)
        # the reader's chat: the parts it's given, what it says, the searches and lookups it runs, what it adds
        self.reader = Gtk.Expander(expanded=True)
        self.reader_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=4)
        self.reader_scroll = Gtk.ScrolledWindow(child=self.reader_box, max_content_height=420,
                                                propagate_natural_height=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.reader.set_child(self.reader_scroll)
        self.reader_shown, self.reader_cards = None, {}  # (the reader_chat drawn, and how much; its tool cards)
        top.append(self.reader)
        self.empty = empty_label("")
        self.empty.set_wrap(True)
        self.empty.set_justify(Gtk.Justification.CENTER)
        top.append(self.empty)
        self.groups = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        top.append(self.groups)
        page = SidePanel._scrolled(top)
        # (at its natural height: at its minimum, the default, the reader's chat is squashed to a line or two)
        page.get_child().set_vscroll_policy(Gtk.ScrollablePolicy.NATURAL)
        self.add_named(page, "list")
        detail = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        head = Gtk.Box(spacing=6, margin_start=8, margin_end=8, margin_top=6, margin_bottom=6)
        back = Gtk.Button(icon_name="go-previous-symbolic", tooltip_text="All names")
        back.add_css_class("flat")
        back.connect("clicked", lambda *_: self.show_list())
        self.detail_title = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, hexpand=True)
        self.detail_title.add_css_class("heading")
        head.append(back)
        head.append(self.detail_title)
        detail.append(head)
        self.detail_body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        detail.append(SidePanel._scrolled(self.detail_body))
        self.add_named(detail, "detail")
        self.rebuild()

    def status_changed(self):
        """What Signal is doing changed: update the status, the log and the skipped names (once a frame)."""
        if self.status_soon is None:
            self.status_soon = GLib.idle_add(self._status_soon)

    def _status_soon(self):
        self.status_soon = None
        ref = self.win.signal
        for w in (self.spinner, self.now):
            w.set_visible(ref is not None)
        self.reader.set_visible(ref is not None and bool(ref.reader_chat))
        if ref is None:
            return False
        busy = ref_busy(ref) and self.win in ref.runners  # (this chat's: another window's chat may run it)
        self.spinner.set_visible(busy)
        self.now.set_label(ref.status_text())
        self.show_reader(ref)
        return False

    def show_reader(self, ref):
        """The reader's chat, the lines it's added since it was last drawn (kept at the bottom if it was there)."""
        chat, box = ref.reader_chat, self.reader_box
        name = ref.agent.model.capitalize() if ref.agent is not None else "Reader"
        self.reader.set_label(name)
        drawn = self.reader_shown
        if drawn is None or drawn[0] is not chat or drawn[1] > len(chat) or \
                (drawn[1] and chat[drawn[1] - 1][0] != drawn[2]):  # (another chat, or older lines dropped)
            while (c := box.get_first_child()) is not None:
                box.remove(c)
                discard(c)
            drawn, self.reader_cards = (chat, 0, None), {}
        if drawn[1] == len(chat):
            return
        adj = self.reader_scroll.get_vadjustment()
        at_end = adj.get_value() + adj.get_page_size() >= adj.get_upper() - 4
        for when, kind, text in chat[drawn[1]:]:
            if kind == "assistant":
                self.reader_message(text)
            elif kind == "results":
                for c in text:
                    if isinstance(c, dict) and c.get("type") == "tool_result" and \
                            (card := self.reader_cards.get(c.get("tool_use_id"))) is not None:
                        card.set_result(c.get("content"), c.get("is_error", False))
            else:
                box.append(self.reader_line(kind, text, when))
        self.reader_shown = (chat, len(chat), chat[-1][0])
        while box.observe_children().get_n_items() > 400:
            first = box.get_first_child()
            box.remove(first)
            discard(first)
        if at_end:
            GLib.idle_add(lambda: adj.set_value(adj.get_upper() - adj.get_page_size()) and False)

    def reader_message(self, content):
        """A message of the reader's, drawn as a subagent's is in its side-panel view: its words as a reply, its tool
        calls as cards (their results come with the next entry). Its words aren't looked up by Signal: they're
        Signal's own."""
        for c in content:
            if c.get("type") == "text" and c.get("text", "").strip():
                self.reader_box.append(MarkdownView(c["text"].strip()))
            elif c.get("type") == "tool_use" and c.get("id") not in self.reader_cards:
                card = self.reader_cards[c["id"]] = ToolCard(c.get("name", ""), c["id"])
                card.set_input(c.get("input") or {})
                self.reader_box.append(card)

    @staticmethod
    def reader_line(kind, text, when):
        """One of Baude's lines in the reader's chat (a part given, what the checks kept, a part done), quieter
        than the reader's own messages."""
        e = html.escape
        if kind == "glossary":
            rows, parts = text["rows"], []
            links = [r for r in rows if r["verdict"] == "link"]
            if links:
                parts.append("Linked " + ", ".join(f"<tt>{e(r['surface'])}</tt>" + (f" ({e(r['kind'])})" if r["kind"]
                                                                                    else "") for r in links[:30]) +
                             (f" and {len(links) - 30} more" if len(links) > 30 else ""))
            absent = [r for r in rows if r["verdict"] == "not_in_code"]
            if absent:
                parts.append("Not in the code: " + ", ".join(f"<tt>{e(r['surface'])}</tt>" for r in absent[:20]))
            plain = [r for r in rows if r["verdict"] == "plain"]
            if plain:
                parts.append("Plain words: " + ", ".join(e(r["surface"]) for r in plain[:20]))
            if text["rejected"]:
                parts.append(f"{text['rejected']} rejected by the checks")
            label = Gtk.Label(xalign=0, wrap=True, selectable=True, wrap_mode=Pango.WrapMode.WORD_CHAR,
                              use_markup=True, label="\n".join(parts))
            label.add_css_class("reader-glossary")
        else:
            label = Gtk.Label(xalign=0, wrap=True, selectable=True, wrap_mode=Pango.WrapMode.WORD_CHAR, label=text)
            label.add_css_class("reader-refused" if kind == "refused" else "reader-part")
        label.set_tooltip_text(time.strftime("%H:%M:%S", time.localtime(when)))
        return label

    def changed(self):
        """Something changed: rebuild once, before the next frame."""
        if self.soon is None:
            self.soon = GLib.idle_add(self._rebuild_soon)

    def _rebuild_soon(self):
        self.soon = None
        self.rebuild()
        return False

    def shown(self):
        """The refs listed now: this chat's names, filtered (plain-text guesses that came to nothing aren't names)."""
        ref, want = self.win.signal, self.search.get_text().strip().lower()
        if ref is None:
            return []
        out = []
        linked = {s for (_m, s), d in ref.decisions.items() if d["verdict"] == "link"}  # (the reader's own names too)
        for s in list(self.win.ref_surfaces) + sorted(linked - set(self.win.ref_surfaces)):
            r = ref.refs.get(s)
            if r is None or (r.status in ("not_found", "pending") and s not in ref.strong) or r.data.get("plain"):
                continue
            if want and want not in s.lower() and want not in r.canonical.lower():
                continue
            out.append(r)
        return out

    def rebuild(self):
        while (c := self.groups.get_first_child()) is not None:
            self.groups.remove(c)
            discard(c)
        self.status_changed()
        if self.win.signal is None:
            self.empty.set_label("Signal is off. Turned on (here, or with the link button in the header), the "
                                 "files, code, commits, env vars, routes, Kubernetes objects and tools Claude names "
                                 "become links, listed here.")
            self.empty.set_visible(True)
            self.summary.set_visible(False)
            return
        refs = self.shown()
        counts = {}
        for r in refs:
            counts[r.status] = counts.get(r.status, 0) + 1
        parts = [f"{counts[k]} {label}" for k, label in (("resolved", "linked"), ("ambiguous", "with several matches"),
                                                          ("pending", "looking up"), ("not_found", "not found"),
                                                          ("stale", "stale")) if counts.get(k)]
        self.summary.set_label(" · ".join(parts) + f"\nGlossary: {pretty_path(self.win.signal.dir)}")
        self.summary.set_visible(True)
        self.empty.set_label("No names yet: the files, commits, env vars and routes in Claude's replies show up here.")
        self.empty.set_visible(not refs)
        self.refresh_btn.set_sensitive(bool(counts.get("not_found")))
        def group(r):  # (a name being looked up may be anything; a table is a table, not a code symbol)
            if r.status == "pending":
                return "pending"
            label = ref_kind_label(r)
            return "db" if label in ("Database table", "Database column", "Database collection", "Database") else r.kind
        groups = [("pending", "Looking up")] + [(k, many) for k, (_one, many) in REF_KINDS.items()]
        groups.insert(groups.index(("symbol", REF_KINDS["symbol"][1])) + 1, ("db", "Database tables and columns"))
        for kind, many in groups:
            mine = [r for r in refs if group(r) == kind]
            if not mine:
                continue
            heading = Gtk.Label(label=many, xalign=0)
            heading.add_css_class("ref-kind")
            self.groups.append(heading)
            lst = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
            lst.add_css_class("boxed-list")
            lst.connect("row-activated", lambda _l, row: self.show_ref(row.surface))
            for r in mine:
                lst.append(self.row(r))
            self.groups.append(lst)
        if self.current and self.get_visible_child_name() == "detail":
            self.show_ref(self.current)

    def row(self, r):
        row = Gtk.ListBoxRow(activatable=True)
        row.surface = r.surface
        box = Gtk.Box(spacing=8, margin_top=5, margin_bottom=5, margin_start=8, margin_end=8)
        icon, css = self.ICONS.get(r.status, self.ICONS["pending"])
        img = Gtk.Image.new_from_icon_name(icon)
        if css:
            img.add_css_class(css)
        box.append(img)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        name = Gtk.Label(label=r.surface, xalign=0, ellipsize=Pango.EllipsizeMode.MIDDLE)
        name.add_css_class("mono")
        sub = Gtk.Label(label=r.summary(), xalign=0, ellipsize=Pango.EllipsizeMode.END)
        sub.add_css_class("dim-label")
        sub.add_css_class("caption")
        text.append(name)
        text.append(sub)
        box.append(text)
        row.set_child(box)
        row.set_tooltip_text(r.summary())
        return row

    def show_list(self):
        self.current = None
        self.set_visible_child_name("list")

    def show_ref(self, surface):
        """A name's card: what it is, how it was found, its locations (each opens), Refresh."""
        r = self.win.signal.refs.get(surface) if self.win.signal else None
        if r is None:
            return
        self.current = surface
        self.detail_title.set_text(surface)
        body = self.detail_body
        while (c := body.get_first_child()) is not None:
            body.remove(c)
            discard(c)
        body.append(sel_label(f'<span alpha="65%">{REF_KINDS.get(r.kind, ("Name",))[0]}</span>  ' +
                              name_markup(r.canonical)))
        if not (r.status == "resolved" and (r.kind in ("commit", "branch", "pr", "tool", "subagent", "skill") or
                                            r.data.get("context"))):  # (their facts say it all)
            status = ref_status_tip(Gtk.Label(label=ref_status_text(r), xalign=0, wrap=True), r)
            status.add_css_class("warning" if r.status == "stale" else "dim-label")
            body.append(status)
        for markup in self.facts(r):
            body.append(sel_label(markup))
        if known := ref_known_text(self.win, surface):
            body.append(sel_label(html.escape(known), css="dim-label"))
        if r.kind == "tool" and r.status == "resolved":
            self.tool_card(r, body)
        if r.locations:
            heading = Gtk.Label(label="Locations", xalign=0)
            heading.add_css_class("ref-kind")
            body.append(heading)
            locs = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            ref_location_buttons(self.win, r, locs)
            body.append(locs)
            first = r.locations[0]
            if first.get("state"):
                body.append(sel_label(f'<span alpha="60%">Read from {html.escape(first["state"])} content'
                                      + (f' at {html.escape((first.get("commit_sha") or "")[:9])}'
                                         if first.get("commit_sha") else "")
                                      + (f' on {html.escape(first["branch"])}' if first.get("branch") else "")
                                      + "</span>", css="caption"))
        if names := ref_run_names(r):
            kube_running_rows(self.win, names, body)
        refresh = Gtk.Button(label="Refresh", tooltip_text="Search again, ignoring what was found or given up on")
        refresh.connect("clicked", lambda *_: self.win.signal.refresh([surface], self.win.touched_paths()))
        actions = Gtk.Box(spacing=6)
        actions.append(refresh)
        if r.kind in ("commit", "branch") and r.status == "resolved":
            git = Gtk.Button(label="Show in Git", tooltip_text="Its place in the history, on the Git page")
            git.connect("clicked", lambda *_: self.win.show_git_ref(r))
            actions.append(git)
        body.append(actions)
        self.set_visible_child_name("detail")

    def tool_card(self, r, body):
        """A tool's inputs (a table), its output type, and this chat's calls to it."""
        d, name = r.data, r.data.get("tool") or r.canonical
        fields, e = d.get("input"), html.escape
        if fields:  # (a list, not a table: the panel is narrow and the descriptions matter most)
            heading = Gtk.Label(label="Inputs", xalign=0)
            heading.add_css_class("ref-kind")
            body.append(heading)
            box = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
            box.add_css_class("boxed-list")
            for f in fields:
                line = f"<tt><b>{e(f['name'])}</b></tt>" + (f"  <tt>{e(f['type'][:200])}</tt>" if f.get("type") else "") \
                    + ('  <span alpha="60%">required</span>' if f.get("required") else "") \
                    + (f'\n<span alpha="75%">{e(f["doc"])}</span>' if f.get("doc") else "")
                lbl = sel_label(line)
                for side in ("top", "bottom"):
                    getattr(lbl, f"set_margin_{side}")(6)
                for side in ("start", "end"):
                    getattr(lbl, f"set_margin_{side}")(10)
                box.append(lbl)
            body.append(box)
        elif fields is not None:
            body.append(sel_label('<span alpha="60%">No inputs</span>'))
        if d.get("output_text"):
            out = Gtk.Expander(label="Output")
            out.set_child(CodeBlock(d["output_text"], "ts"))
            body.append(out)
        calls = [c for c in self.win.tool_cards.values() if c.name == name and c.input]
        if calls:
            body.append(sel_label(f"<b>Called {len(calls)} time{'s' * (len(calls) != 1)} in this chat</b>"
                                  + (", the last ones:" if len(calls) > 1 else ":")))
            for c in calls[-2:]:
                body.append(CodeBlock(json.dumps(c.input, indent=2, ensure_ascii=False)[:1500], "json"))
        if name in ("Agent", "Task") and SUBAGENTS:
            types = ", ".join(f"{n} ({'built in' if i.get('built_in') else 'defined here'})" for n, i in SUBAGENTS.items())
            body.append(sel_label(f'<span alpha="70%">Subagent types: {html.escape(types)}</span>'))
        if name == "Skill" and SKILLS:
            body.append(sel_label(f'<span alpha="70%">Skills: {html.escape(", ".join(SKILLS))}</span>'))

    @staticmethod
    def facts(r):
        """Lines of markup about a commit, branch or pull request (GitHub links, dates, LOC, PRs), or a tool,
        subagent type or skill (what it is, where its schema came from)."""
        d, e, out = r.data, html.escape, []
        link = lambda url, text: f'<a href="{e(url)}">{e(text)}</a>'  # noqa: E731
        if r.status != "resolved":
            return out
        if r.kind == "tool":
            src = d.get("source")
            if d.get("description"):
                out.append(e(d["description"]))
            if "permission" in d:
                out.append("Asks your permission: " + ("yes" if d["permission"] else "no"))
            if src == "anthropic":
                where = f"Claude Code {d.get('cli') or ''}".strip() + (f" · Anthropic's Agent SDK {d['sdk']} types"
                                                                       if d.get("sdk") and d.get("input") is not None else "")
                docs = TOOL_DOCS_PAGE + (f"#{d['anchor']}" if d.get("anchor") else "")
                out.append(f'<span alpha="70%">{e(where)} · </span>{link(docs, "Docs ↗")}')
            elif src == "mcp":
                out.append(f'<span alpha="70%">From the MCP server {e(d.get("server", ""))} (its tools/list)</span>')
            elif d.get("server"):
                out.append(f'<span alpha="70%">A tool of the MCP server {e(d["server"])}, which Claude Desktop provides: '
                           "no public schema, so only its calls show what it takes</span>")
            else:
                out.append('<span alpha="70%">No schema here yet: turn on Tools at the top of this page to fetch '
                           "Claude Code's from Anthropic</span>")
            return out
        if d.get("context"):  # (a kubeconfig context: what kubectl would use, never its credentials)
            out.append(f"Kubeconfig {'cluster' if d.get('cluster_only') else 'context'} <b>{e(d['context'])}</b>" +
                       (" (the current one)" if d.get("current") else ""))
            for key, label in (("cluster", "Cluster"), ("server", "Server"), ("namespace", "Namespace"), ("user", "User")):
                if d.get(key):
                    out.append(f"{label}: <tt>{e(str(d[key]))}</tt>")
            out.append(f'<span alpha="70%">In {e(pretty_path(d.get("kubeconfig") or ""))}' +
                       (f", line {d['line']}" if d.get("line") else "") + "; its credentials aren't read</span>")
            return out
        if r.kind == "k8s" and d.get("kind"):
            out.append(f"<b>{e(d['kind'])}</b> <tt>{e(d.get('name') or '')}</tt>" +
                       (f" in namespace <tt>{e(d['namespace'])}</tt>" if d.get("namespace") else ""))
            if d.get("chart") is not None:
                out.append(f"Helm chart <tt>{e(d['chart'] or '.')}</tt>" +
                           (f", release <tt>{e(d['release'])}</tt>" if d.get("release") else ""))
            if d.get("overlay"):
                out.append(f"Kustomize overlay <tt>{e(d['overlay'])}</tt>")
            if d.get("how"):
                out.append(f'<span alpha="70%">{e(d["how"])}</span>')
            return out
        if r.kind in ("subagent", "skill"):
            if d.get("description"):
                out.append(e(d["description"]))
            for key, label in (("model", "Model"), ("tools", "Tools")):
                if d.get(key):
                    out.append(f"{label}: <tt>{e(str(d[key]))}</tt>")
            if d.get("built_in"):
                out.append('<span alpha="70%">Built into Claude Code</span>')
            return out
        if r.kind == "commit":
            out.append(f"<b>{e(d.get('subject', ''))}</b>")
            if d.get("range"):
                out.append(f"{d.get('count', 0)} commit{'s' * (d.get('count') != 1)}, "
                           f"<tt>{e(d['range'][:9])}..{e(d.get('sha', '')[:9])}</tt>")
            if d.get("repo"):
                out.append(f"In <tt>{e(d['repo'].replace(os.path.expanduser('~'), '~', 1))}</tt>")
            out.append(f"{e(d.get('author', ''))} · {e(ref_date(d.get('date')))}")
            out.append(f"+{d.get('added', 0)} −{d.get('removed', 0)} in {d.get('files', 0)} files")
            out.append(link(d["url"], "Open on GitHub ↗") if d.get("url") else
                       "Local only: no remote branch contains it")
            if d.get("rewritten"):
                rep = f" Probably replaced by <tt>{e(d['successor'][:9])}</tt> (same subject)." if d.get("successor") else ""
                out.append(f"On no branch: a rebase or squash replaced it.{rep}")
            elif d.get("branches") or d.get("remotes"):
                on = (d.get("branches") or []) + (d.get("remotes") or [])
                out.append("On " + e(", ".join(on[:8])) + (f" and {len(on) - 8} more" if len(on) > 8 else ""))
        elif r.kind == "branch":
            out.append(f"<b>{e(d.get('subject', ''))}</b>")
            out.append(f"Last commit {e(ref_date(d.get('date')))} · <tt>{e(d.get('sha', '')[:9])}</tt>")
            if "ahead" in d:
                out.append(f"{d['ahead']} ahead, {d['behind']} behind {e(d.get('base', ''))}")
            if d.get("upstream"):
                out.append(f"Tracks {e(d['upstream'])}")
            out.append(link(d["url"], "Open on GitHub ↗") if d.get("url") else "Not pushed")
        elif r.kind == "pr":
            out.append(f"<b>{e(d.get('title') or '#' + str(d.get('number')))}</b>")
            if d.get("state"):
                dates = [f"opened {ref_date(d.get('created'))}"]
                if d.get("merged"):
                    dates.append(f"merged {ref_date(d['merged'])}")
                elif d.get("closed"):
                    dates.append(f"closed {ref_date(d['closed'])}")
                out.append(f"{e(d['state'].capitalize())}: " + e(", ".join(dates)))
            if d.get("additions") is not None:
                out.append(f"+{d['additions']} −{d['deletions']} in {d.get('changed_files', 0)} files"
                           + (f" · {e(d.get('head') or '')} → {e(d.get('base') or '')}" if d.get("head") else ""))
            out.append(link(d["url"], "Open on GitHub ↗"))
        if r.kind in ("commit", "branch"):
            if d.get("prs"):
                out.append("<b>Pull requests</b>")
                for p in d["prs"]:
                    when = f"merged {ref_date(p['merged'])}" if p.get("merged") else \
                        f"{p.get('state', '')}, opened {ref_date(p.get('created'))}"
                    out.append(f"{link(p['url'], '#' + str(p['number']))} {e(p.get('title', ''))} ({e(when)})")
            elif d.get("pushed") and "prs" in d:
                out.append("No pull requests")
            elif d.get("pushed") and not shutil.which("gh"):
                out.append('<span alpha="60%">Install the GitHub CLI (gh) to see pull requests</span>')
        return out

    def set_sonnet(self, on):
        save_settings(signal_reader=on)
        if self.win.signal is not None:
            self.win.signal.set_agent(on)

    def set_tool_schemas(self, on):
        if load_settings().get("signal_tool_schemas") is on:
            return
        if self.win.signal is not None:
            self.win.signal.set_tool_schemas(on)
        else:
            save_settings(signal_tool_schemas=on)

    def set_lsp(self, on):
        if load_settings().get("signal_lsp") is on:
            return
        if self.win.signal is not None:
            self.win.signal.set_lsp(on)
        else:
            save_settings(signal_lsp=on)

    def refresh_not_found(self):
        ref = self.win.signal
        if ref is not None:
            ref.refresh([r.surface for r in self.shown() if r.status == "not_found"], self.win.touched_paths())


def in_chat(widget):
    """Whether a widget is in a chat on the screen, what the chat's zoom sizes (.zoomable: the chat, its box, a
    subagent's chat in the side panel). Not the box hidden under a code tab, which the Kubernetes and database views
    give the focus back to as they close."""
    if widget is None or not widget.get_mapped():
        return False
    while widget is not None and not widget.has_css_class("zoomable"):
        widget = widget.get_parent()
    return widget is not None


HOVER_POOL = WorkerPool(1)  # (db_prefetch's)


def kube_page_for(resource):
    """The Kubernetes view's page for a kind kubectl calls resource (Pods if it has none)."""
    return next((p for _title, pages in KUBE_SECTIONS for p in pages if p["resource"] == resource), KUBE_PODS)


def pg_forget():
    """Read the servers, databases and schemas again next time (Try again)."""
    PG_LISTS.clear()
    PG_CACHE.clear()
    _FORGOTTEN[0] += 1  # (a lookup on its way began before this: not joined)


def db_prefetch(target):
    """Look for (table, column, hint) now, for the click that may follow (BaudeWindow.hover_link), on a worker of its
    own: only the last name the pointer went onto is looked for, and a click never waits behind it (it joins it, if
    it's for the same name)."""
    _PREFETCH[0] = target
    HOVER_POOL.submit(_prefetch)


def _prefetch():
    target, _PREFETCH[0] = _PREFETCH[0], None
    if target is not None and (*target, _FORGOTTEN[0]) not in _LOCATING:
        db_locate(*target)


_PREFETCH = [None]


def db_named(surface):
    """Where the live databases have a name written in a reply: a table or collection of that name, or (an
    entity's collection is e_<Entity>, a relation's r_<RELATION>) one with that prefix, or a database of
    that name. [(container, database, table or None)], or []."""
    index = db_names()
    for name in (surface, f"e_{surface}", f"r_{surface}"):
        if index.get(name):
            return index[name]
    return []


def _num(path):
    return lambda o, c: _ks(o, *path)


class SettingsDialog(Adw.PreferencesDialog):
    """Menu → Settings… (⌘, on macOS, Ctrl+, elsewhere): Meta's (its model, Code mode), full-width text, the code font,
    the accent colour, Baude in
    the platform's list of apps, feedback, and the sizes, 1.0 the usual: the text's and the icons', all of them in every
    window (apply_scales), and the chat's zoom that ⌘0 / Ctrl+0 goes back to. Each takes as it's changed (the code font
    from the next start) and is kept in settings.json. (The switches are the window's actions, full-width's the menu's
    too, so the two agree; the handlers are given the row, never a closure over this dialog: see discard)"""

    def __init__(self, win):
        super().__init__(title="Settings")
        page = widgets.settings_page()
        page.add(meta.settings_group(win.panel.meta))  # (Record's too, and the same settings)
        group = Adw.PreferencesGroup()
        group.add(self.switch("Full-width text", None, "win.fullwidth"))
        group.add(widgets.settings_code_font(win, "Baude"))
        group.add(widgets.settings_accent())
        group.add(self.switch(f"Show in {LAUNCHER.where}", LAUNCHER.paths()[0].replace(os.path.expanduser("~"), "~", 1),
                              "win.launcher"))
        group.add(update.settings_row())  # (Record's too: the one setting)
        self.version_row = update.version_row(self)  # (and the version, its changelog: Record's too)
        group.add(self.version_row)
        page.add(group)
        sizes = Adw.PreferencesGroup(title="Size")
        self.text_row = self.size_row("Text Scale", None, scale_setting("text_scale"),
                                      lambda size: win.set_scale("text_scale", size))
        self.icon_row = self.size_row("Icon Scale", None, scale_setting("icon_scale"),
                                      lambda size: win.set_scale("icon_scale", size))
        self.zoom_row = self.size_row("Global Scale", None, win.zoom_default,  # (the chat's zoom: what ⌘0 goes back to)
                                      win.set_zoom_default, ZOOM_MIN, ZOOM_MAX)
        for row in (self.text_row, self.icon_row, self.zoom_row):
            sizes.add(row)
        page.add(sizes)
        group = Adw.PreferencesGroup()
        self.feedback_row = Adw.ButtonRow(title="Send Feedback…", end_icon_name="go-next-symbolic")
        # (this goes first: the captures are of the window, not of this over it)
        self.feedback_row.connect("activated", lambda row: (row.get_ancestor(Adw.Dialog).close(), win.open_feedback()))
        group.add(self.feedback_row)
        page.add(group)
        self.add(page)

    switch = staticmethod(settings_switch)  # (widgets': Record's Settings has them too)
    size_row = staticmethod(settings_size)


class Repaint(Gtk.Widget):
    """Over the whole window, drawing nothing but for a frame when asked (flash): a change across all of it, which makes
    all of that frame's picture new. GTK 4.24's macOS backend shows a window as tiles, each given the new picture only
    where a frame changed something; as a dialog's opening settles over a maximized window, some lose theirs, and the
    app beneath shows through everywhere but the dialog until the whole window changes (see dialog_shown). Not
    needed while every frame draws all of the window (macos.full_redraws, on by default)."""

    TINT = Gdk.RGBA()
    TINT.red = TINT.green = TINT.blue = 0.0
    TINT.alpha = 2 / 255  # (black, all but clear: not to be seen, but a change; 1/255 is about where GDK's clear ends)

    def __init__(self):
        super().__init__(can_target=False, can_focus=False)
        self.frames, self.flashes = None, 0  # (frames: the flash's so far, None while not lit)

    def flash(self):
        self.flashes += 1
        if self.frames is None:
            self.add_tick_callback(Repaint.tick)
        self.frames = 0
        self.queue_draw()

    @staticmethod
    def tick(self, _clock):
        """At the start of a frame: lit for the one after the flash (a tick comes before the frame's drawing), then
        not."""
        self.frames += 1
        if self.frames < 2:
            return True
        self.frames = None
        self.queue_draw()
        return False

    def do_snapshot(self, snap):
        if self.frames is not None:
            snap.append_color(Repaint.TINT, Graphene.Rect().init(0, 0, self.get_width(), self.get_height()))


class BaudeWindow(Adw.ApplicationWindow, FeedbackHost, widgets.AccentFade):
    def do_snapshot(self, snapshot):
        self.snapshot_fading(snapshot, Adw.ApplicationWindow)  # (widgets.AccentFade: the accent changed, fading)

    def __init__(self, app, opts):
        super().__init__(application=app, title="Baude", default_width=900, default_height=800)
        self.cwd = self.run_cwd = None  # set through set_cwd() once the header button exists
        self.proc = None
        self.session_id = opts.resume
        self.remote = None  # the open chat's server, when it's a session on the network (open_remote)
        self.remote_syncing = {}  # (server key, session id) -> the fetch of its transcript under way (sync_remote)
        self.busy = False
        self.total_cost = 0.0
        self.always_allow = set()
        self.attachments = []
        self.chat_sent, self.listed_begun = False, None  # (the sidebar lists the open chat once it's begun)
        self.pending = {}          # control request id -> (callback(response), refused(error) or None) (request)
        self.tasks = {}            # background task id -> latest info (commands and subagents)
        self.waiting_cards = set()  # permission prompts not answered yet
        self.background = {}  # session id -> the claude of a chat left mid-turn, going on with it (detach_proc)
        self.desktop_chat = False  # (the chat's Claude Desktop's: its claude runs here, not in the host process)
        # a past session opens read-only: its file is shown (and tailed) but no claude touches it
        # until you send a message (even resuming without a message writes to the file)
        self.resume_pending = False
        self.view_path = None
        self.tail = livefeed.Tail(self.tail_lines)  # the viewed transcript, followed as it's written
        self.resumed_note = False   # a viewed session's history is shown: mark where you took it up
        self.own_mtimes = {}        # transcript path -> its mtime when baude's own claude let go of it
        self.turn_finished = False
        self.artifacts = {}        # artifact url -> info (title, icon, version, ...)
        self.signal, self.ref_checkout = None, None     # the chat folder's Signal while it's on
        self.ref_surfaces = {}     # names the replies shown here mentioned, in order (the Signal page)
        self.reply_views = []      # Claude's replies drawn here (relinked as Signal learns their names)
        self.pointer = (0.0, 0.0)  # where the pointer is (a Signal popover points at the link clicked)
        self.render_target = None  # where add() puts top-level widgets (None: the transcript)
        self.orphan_results = {}   # tool_use_id -> result whose call isn't drawn yet
        self.history = None        # a loaded session's items not drawn yet (HistoryIndex)
        self.chunk_slots = None    # the places of its earlier chunks not drawn yet (make_chunk_slots)
        self.estimated_per = 0     # the px per item they're sized by (estimate_slots)
        self.history_refs = None   # its replies Signal's being given, a few at a time (queue_history_refs)
        self.history_path = None   # its transcript (subagents live in <it without .jsonl>/subagents/)
        self.sub_tails = {}        # agent id -> how far its transcript has been read
        ARTIFACT_TITLES.clear()    # titles belong to the conversation in this window
        self.models_info = {}      # model value -> initialize's model entry
        self.effort = None
        self.local_effort = "off"  # (a model on this Mac that thinks: its own level, Claude's effort apart; none at first)
        self.settings = load_settings()
        self.zoom = min(ZOOM_MAX, max(ZOOM_MIN, float(load_settings().get("zoom", 1.0))))
        self.zoom_default = min(ZOOM_MAX, max(ZOOM_MIN, float(self.settings.get("zoom_default") or 1.0)))  # (Ctrl+0's)
        self.zoom_css = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(self.get_display(), self.zoom_css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
        self.reset_stream_state()

        # header
        header = Adw.HeaderBar()
        # None = let the CLI decide (settings / account default); shown once it reports back
        self.model = None if opts.model in (None, "default") else opts.model
        self.mode = "plan"  # read-only until you pick another mode
        self.model_dd = ChoiceDropDown([], "Model", self.on_model_changed)
        self.mode_dd = ChoiceDropDown([], "Permission mode", self.on_mode_changed)
        self.effort_dd = ChoiceDropDown([], "Thinking effort", self.on_effort_changed)
        self.effort_dd.set_sensitive(False)
        self.set_cwd(opts.cwd)
        self.sidebar_btn = Gtk.ToggleButton(icon_name="sidebar-show-symbolic",
                                            tooltip_text="Sessions sidebar (Ctrl+B)")
        header.pack_start(self.sidebar_btn)
        # (the chat's name in the text's own weight: a .heading's bold was the one bold thing up here)
        self.title_label = Gtk.Label(label="New session", max_width_chars=48, ellipsize=Pango.EllipsizeMode.END,
                                     tooltip_text="Rename: right-click the session in the sidebar, or F2")
        header.set_title_widget(self.title_label)
        self.panel_btn = Gtk.ToggleButton(icon_name="sidebar-show-right-symbolic",
                                          tooltip_text="Side panel: subagents, processes, code, files (Ctrl+Alt+B)")
        menu = Gio.Menu()
        item = Gio.MenuItem.new("Settings…", "win.settings")
        item.set_attribute_value("accel", GLib.Variant.new_string(SETTINGS_KEY))  # (its key, shown beside it)
        menu.append_item(item)
        menu.append("Full-width text", "win.fullwidth")
        menu.append("Read-only session", "win.readonly")
        menu.append("Database Schemas…", "win.dbschema")
        menu.append("Kubernetes…", "win.kube")
        menu.append(record_item(), "win.record")
        item = Gio.MenuItem.new("Send Feedback…", "win.feedback")
        item.set_attribute_value("accel", GLib.Variant.new_string(widgets.FEEDBACK_KEY))
        menu.append_item(item)
        menu.append("Ongoing Feedback", "win.fixes")
        self.main_menu = menu
        menu.append("Restart Baude", "win.restart")
        menu_btn = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu, tooltip_text="Menu")
        self.signal_btn = Gtk.MenuButton(icon_name=themed_icon("insert-link-symbolic", "emblem-symbolic-link-symbolic"),
                                         popover=self.signal_menu())
        header.pack_end(menu_btn)
        header.pack_end(self.panel_btn)
        header.pack_end(self.signal_btn)
        header.pack_end(self.mode_dd)
        header.pack_end(self.effort_dd)
        header.pack_end(self.model_dd)
        prefs = Gio.SimpleAction.new("settings", None)
        prefs.connect("activate", lambda *_: self.open_settings())
        self.add_action(prefs)
        fullwidth = Gio.SimpleAction.new_stateful(
            "fullwidth", None, GLib.Variant.new_boolean(bool(self.settings.get("full_width"))))
        fullwidth.connect("change-state", self.on_fullwidth_toggled)
        self.add_action(fullwidth)
        launcher = Gio.SimpleAction.new_stateful("launcher", None,
                                                 GLib.Variant.new_boolean(LAUNCHER.installed()))
        launcher.connect("change-state", self.on_launcher_toggled)
        self.add_action(launcher)
        readonly = Gio.SimpleAction.new_stateful("readonly", None, GLib.Variant.new_boolean(False))
        readonly.connect("change-state", lambda a, v: self.set_read_only(v.get_boolean()))
        self.add_action(readonly)
        dbschema = Gio.SimpleAction.new("dbschema", None)
        dbschema.connect("activate", lambda *_: self.schema.open())
        self.add_action(dbschema)
        kube = Gio.SimpleAction.new("kube", None)
        kube.connect("activate", lambda *_: self.kube.open())
        self.add_action(kube)
        record = Gio.SimpleAction.new("record", None)
        record.connect("activate", lambda *_: self.open_record())
        self.add_action(record)
        feedback = Gio.SimpleAction.new("feedback", None)
        feedback.connect("activate", lambda *_: self.open_feedback())
        self.add_action(feedback)
        fixes_action = Gio.SimpleAction.new("fixes", None)
        fixes_action.connect("activate", lambda *_: self.open_fixes())
        self.add_action(fixes_action)
        self.feedback_draft = None
        restart = Gio.SimpleAction.new("restart", None)
        restart.connect("activate", lambda *_: self.restart_app())
        self.add_action(restart)

        # transcript
        self.transcript = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                                  margin_top=12, margin_bottom=12, margin_start=16, margin_end=16)
        self.transcript.add_css_class("zoomable")
        over = Gtk.Overlay(child=self.transcript)
        self.clamp = Adw.Clamp(child=over)
        # (at its natural height, its code blocks at most 2/3 of it: widgets.markdown_scroller, Record's Read's too)
        self.scroller = widgets.markdown_scroller(self.clamp, vexpand=True)
        self.text_selection = widgets.ChatSelection(self.transcript, self.scroller, over)  # (Record's Read's too)
        self.apply_full_width(bool(self.settings.get("full_width")))
        self.follow = livefeed.FollowEnd(self.scroller)  # (the newest in view, once it's read to: on_layout)
        self.restore_scroll = None  # (distance from the bottom, until) after a restart
        self.toggle_held = self.slack = None  # (a toggle clicked in the chat, till it's done sliding: hold_toggle)
        vadj = self.scroller.get_vadjustment()
        vadj.connect("value-changed", self.on_scrolled)
        wheel = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL)
        wheel.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        wheel.connect("scroll", self.on_reader_scroll)
        self.scroller.add_controller(wheel)
        # (its own scrolling and glide, each a step from where the view is: GTK's glide undid the view's moving by a
        # chunk of history drawn above it, mid-glide)
        self.scroll_glide = widgets.ScrollGlide(self.scroller, lambda: bool(self.history and self.history.more),
                                                rested=self.chat_still, moved=self.reader_moved)
        # (the reader's other ways of moving it, as against GTK clamping it, the view held, or the end followed: the
        # scrollbar while it's held, the keys while they're pressed and a moment after, as GTK eases to where they go)
        self.thumb_held, self.keyed_at = False, 0.0
        thumb = Gtk.EventControllerLegacy(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        thumb.connect("event", self.on_scrollbar_event)
        self.scroller.get_vscrollbar().add_controller(thumb)
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", lambda *_: setattr(self, "keyed_at", time.monotonic()) or False)
        self.scroller.add_controller(keys)
        self.connect("realize", lambda *_: self.get_frame_clock().connect_after("layout", self.on_layout))
        # (while it's scrolled: see reader_moved; the earlier chunks drawn a little a frame: see fill_frame)
        self.moved_at, self.still_timer, self.cull_at = 0.0, None, None
        self.fill_jobs, self.fill_tick, self.fill_reveal, self.estimate_tick = collections.deque(), None, 1, None
        self.fill_sweeping, self.above_changed, self.place_at = False, False, None

        # composer (widgets.Composer: Record's too)
        self.attach_btn = Gtk.Button(icon_name="list-add-symbolic",
                                     tooltip_text="Attach images, PDFs or files (or paste / drop them)")
        self.attach_btn.add_css_class("flat")
        self.attach_btn.connect("clicked", self.on_pick_attachments)
        box_overlay = self.composer_box = Composer(
            buttons=[self.attach_btn],
            send_tip="Send (Enter, or Ctrl+Enter inside a code block)\nNew line: Shift+Enter\n"
                     "@: a chat, a file, a pull request…")
        self.input, self.composer_md, self.send_btn = box_overlay.input, box_overlay.markdown, box_overlay.send
        self.input.get_buffer().connect("changed", lambda *_: self.draft_changed())
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self.on_key)
        self.input.add_controller(keys)
        self.send_btn.connect("clicked", lambda *_: self.on_send())
        self.attach_row = Gtk.Box(spacing=6)
        self.attach_strip = Gtk.ScrolledWindow(child=self.attach_row, visible=False, vexpand=False,
                                               vscrollbar_policy=Gtk.PolicyType.NEVER,
                                               propagate_natural_height=True)
        self.completer = SlashCompleter(self.input, box_overlay)
        # (@'s list, as in Meta's box: the chats, and the names the chat's folder has)
        self.refs = meta.RefCompleter(lambda: self.panel.meta.names if getattr(self, "panel", None) else None,
                                      self.input, box_overlay)
        composer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                           margin_start=12, margin_end=12, margin_bottom=6)
        composer.append(self.attach_strip)
        composer.append(box_overlay)
        # (☰ → Read-only session hides it, and so does another program having the session: show_composer)
        self.composer, self.read_only, self.locked = composer, False, False
        self.set_send_button(busy=False)

        self.status = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, hexpand=True)
        ctx = self.ctx_box = widgets.ContextUsage()  # (Meta's too)
        self.ctx_label, self.ctx_bar = ctx.label, ctx.bar
        statusbar = Gtk.Box(spacing=12)
        statusbar.add_css_class("statusbar")
        self.working_dots = WorkingDots()
        statusbar.append(self.working_dots)
        statusbar.append(self.status)
        self.tasks_btn = Gtk.Button(visible=False, tooltip_text="Background tasks")
        self.tasks_btn.add_css_class("flat")
        self.tasks_btn.add_css_class("tasks-indicator")
        self.tasks_btn.connect("clicked", lambda *_: self.show_panel(True))
        statusbar.append(self.tasks_btn)
        statusbar.append(ctx)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.chat_column = root
        # (a press in it: the chat's under the pointer again at once, before the chat's own controllers hear it, so a
        # selection dragged from there finds its labels, and a click after this one lands where it's made)
        press = Gtk.GestureClick(button=0, propagation_phase=Gtk.PropagationPhase.CAPTURE)
        press.connect("pressed", lambda *_: self.chat_still())
        root.add_controller(press)
        self.chat_fade = Fade(root)  # it fades lighter while the chat is swapped (switch_to)
        # the main area's tabs (widgets.MainTabs, Record's too): the chat, pinned first, and code clicked in it, each
        # file a tab of its own (show_code); the tabs' bar only while there's code open
        # (⌥⌘← ⌥⌘→: back and forward through them)
        self.tabs = widgets.MainTabs(shown_alone=False, reopen=self.reopen_tab, closed=BaudeWindow.tab_closed)
        self.tabs.prepend(self.tabs.bar)
        self.tabs.open("chat", lambda: root, "Chat", icon="baude-claude-symbolic", pinned=True)  # (Claude's logo)
        widgets.inside(self.tabs)  # (the chat: the inside, set into the frame the bar, the sessions and the panel are)
        self.banner = Adw.Banner(revealed=False)
        root.append(self.banner)
        root.append(self.scroller)
        root.append(composer)
        root.append(statusbar)
        self.panel = SidePanel(self)
        # a plain split: same background on both sides, a 1px rule between them, no shadow;
        # drag the rule to resize the panel. It stops where the chat's at its least (what it has, its cards' lines, its
        # composer): the panel never goes over the chat, nor does the sessions' rule push it under the panel
        self.panel.set_visible(False)
        self.panel.set_size_request(320, -1)
        self.split = paned_grip(Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, start_child=self.tabs,
                                          end_child=self.panel, resize_start_child=True, resize_end_child=False,
                                          shrink_start_child=False, shrink_end_child=False, wide_handle=False))
        self.split.add_css_class("main-split")
        self.split.add_css_class("inside-before")
        # the width you drag the panel to is kept: when it opens again, and when it shows code (settings.json:
        # "panel_width"); only a drag of the rule sets it, not the window resizing nor Baude placing it
        self.panel_width, self.split_save = self.settings.get("panel_width"), None
        self.split.connect("notify::position", lambda *_: self.split_dragged())
        self.panel_slide = widgets.PaneSlide(self.split, self.panel, [self.panel_btn])
        self.panel_slide.closed_by_drag = self.panel_closed_by_drag
        self.panel.connect("notify::visible", lambda *_: self.panel.get_visible() and self.place_split())
        self.sidebar = SessionSidebar(self)
        network().listen(self.on_network)
        self.outer = paned_grip(Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, start_child=self.sidebar,
                                          end_child=self.split, resize_start_child=False, resize_end_child=True,
                                          shrink_start_child=False, shrink_end_child=False, wide_handle=False))
        self.outer.add_css_class("main-split")
        self.outer.add_css_class("inside-after")
        # the binding copies the button's state on creation, so set the button first
        self.sidebar.set_visible(self.settings.get("sidebar", True))
        self.sidebar_slide = widgets.PaneSlide(self.outer, self.sidebar, [self.sidebar_btn], end=False)
        self.sidebar.connect("notify::visible", lambda *_: save_settings(sidebar=self.sidebar.get_visible()))
        tv = Adw.ToolbarView()
        tv.add_css_class("baude-frame")  # (the bar, the sessions, the panel: widgets.CSS)
        tv.add_top_bar(header)
        tv.set_content(self.outer)
        self.header_bar = header
        tv.add_bottom_bar(self.make_capture_bar())  # (feedback.FeedbackHost: Capture while one's pending)
        self.toasts = Adw.ToastOverlay(child=tv)
        self.zoom_toast = None
        self.zoom_timer = self.zoom_job = None
        self.zoom_wheel = 0.0  # Ctrl+scroll that hasn't added up to a step yet
        self.lightbox = widgets.Lightbox(self)
        self.schema = SchemaView(self)
        self.kube = KubeView(self)
        host = Gtk.Overlay(child=self.toasts)
        host.add_overlay(self.schema)
        host.add_overlay(self.kube)
        host.add_overlay(self.lightbox)
        self.repaint, self.repaint_wait = Repaint(), None
        host.add_overlay(self.repaint)
        self.set_content(host)
        self.connect("notify::visible-dialog", BaudeWindow.dialog_shown)

        sc = Gtk.ShortcutController(scope=Gtk.ShortcutScope.GLOBAL)
        for triggers, fn in (("<Control>n", lambda: self.switch_to(self.new_session)), ("<Control>o", self.show_sessions),
                             # (Ctrl+B the sessions on the left, as an editor's sidebar; the text box has no Emacs
                             # Ctrl+B to lose. Ctrl+Alt+B the panel on the right, as VS Code's secondary sidebar)
                             ("<Control>b F9", lambda: self.sidebar_slide.toggle()),
                             ("<Control><Alt>b", lambda: self.show_panel(not self.panel_slide.target)),
                             (widgets.META_KEY, self.show_meta),
                             # (⌘K / Ctrl+K: every name in the glossary, searched, as an editor's command palette)
                             (PALETTE_KEY, lambda: RefPalette(self).present(self)),
                             (SETTINGS_KEY, self.open_settings),
                             (zoom_keys("w"), lambda: self.tabs.close_current()),  # (a code tab: the chat stays)
                             (widgets.NAV_KEYS["back"], lambda: self.tabs.back()),  # (⌥⌘ ← → ↑: a browser's
                             (widgets.NAV_KEYS["forward"], lambda: self.tabs.forward()),  # back, forward, up)
                             (widgets.NAV_KEYS["up"], lambda: self.go_up()),
                             (zoom_keys("plus", "<Shift>plus", "equal", "KP_Add"),
                              lambda: self.zoom_schema(+1) or self.zoom_code(+1) or
                              self.set_zoom(self.zoom + ZOOM_STEP)),
                             (zoom_keys("minus", "underscore", "KP_Subtract"),
                              lambda: self.zoom_schema(-1) or self.zoom_code(-1) or
                              self.set_zoom(self.zoom - ZOOM_STEP)),
                             (zoom_keys("0", "KP_0"),
                              lambda: self.zoom_schema(0) or self.zoom_code(0) or self.set_zoom(self.zoom_default))):
            for t in triggers.split():
                sc.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string(t),
                                                 Gtk.CallbackAction.new(lambda *_, fn=fn: fn() or True)))
        # Ctrl+F: the Kubernetes view's filter while it's open (it covers the window, so not the find bar of a Code
        # page under it), else the Code page's find bar if that's showing
        sc.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("<Control>f"),
                                         Gtk.CallbackAction.new(lambda *_: self.kube.find() or self.schema.start_find() or
                                                                                self.find_in_code())))
        self.add_controller(sc)
        ctrl_scroll = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL)
        ctrl_scroll.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        ctrl_scroll.connect("scroll", self.on_ctrl_scroll)
        ctrl_scroll.connect("scroll-end", lambda *_: setattr(self, "zoom_wheel", 0.0))  # a new swipe starts afresh
        self.add_controller(ctrl_scroll)
        drop = Gtk.DropTarget.new(GObject.TYPE_NONE, Gdk.DragAction.COPY)
        drop.set_gtypes([Gdk.FileList, Gdk.Texture])
        drop.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)  # before the TextView's own text drop
        drop.connect("drop", self.on_drop)
        self.add_controller(drop)
        self.apply_zoom()
        apply_scales(self.get_display())
        widgets.apply_accent(self.get_display())  # (Settings → Accent Colour)
        self.connect("close-request", lambda *_: (self.proc and self.proc.kill(),
                                                  [self.let_go_background(p) for p in list(self.background.values())],
                                                  [sq.close() for sq in self.panel.side_questions()],
                                                  self.drop_signal(), GLib.idle_add(self.let_go)) and False)

        motion = Gtk.EventControllerMotion(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        motion.connect("motion", self.on_pointer)
        motion.connect("leave", lambda *_: self.pointer_left())
        self.add_controller(motion)
        self.hover_soon, self.hovered, self.db_prefetched = None, None, (None, 0.0)  # (see hover_link)
        self.signal_pending = None  # (a new chat's switches, till it has an id to keep them under)
        on, links = self.chat_signal(self.session_id)
        self.signal_run.set_active(on)
        self.signal_show.set_active(links)
        self.set_signal(save=False)
        self.connect("notify::is-active", lambda *_: self.is_active() and self.links_on_focus())
        widgets.watch_github(self, BaudeWindow.github_folders, BaudeWindow._github_changed)
        self.signal_run_handler = self.signal_run.connect("notify::active", lambda sw, _p: self.set_signal(on=sw.get_active()))
        self.signal_show_handler = self.signal_show.connect("notify::active",
                                                            lambda sw, _p: self.set_signal(links=sw.get_active()))

        update.watch_input(self)  # (what quiet reads: a new version waits while you're at it)
        widgets.warm_shaders(self)  # (the first window's: after its first frame)
        restore = take_restart_state(getattr(opts, "restore", None))
        if restore:
            self.session_id = restore.get("session_id") or self.session_id
            self.model, self.mode = restore.get("model") or self.model, restore.get("mode") or self.mode
        if restore and restore.get("remote") and (r := network().find(self.session_id)) is not None:
            self.open_remote(remote_info({**r.sessions[self.session_id], "server": r}))  # (its server's, again)
        elif self.session_id and (info := find_session(self.session_id)):
            self.view_session(info["path"])
        self.poll_source = GLib.timeout_add(1000, self.poll_tail)
        if restore:
            self.apply_chat_signal()  # (the restored chat's own switches)
            self.apply_restart_state(restore)
        if self.proc is None:  # (a network session's is its server's, attached to already: open_remote)
            self.start_proc()
        self.input.grab_focus()
        self.apply_read_only()  # (a session opened read-only before)

    # ---- restart, keeping what's on screen
    # ---- feedback (Menu → Send Feedback…)
    def open_record(self):
        """Record (record.py, beside this file) reading this chat, on its Read tab in whichever notebook's open. One
        running already is handed the chat."""
        path = getattr(self, "history_path", None)
        record = os.path.join(os.path.dirname(os.path.realpath(__file__)), "record.py")
        subprocess.Popen([sys.executable, record] + (["--chat", path] if path and os.path.exists(path) else []),
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)

    def feedback_context(self):
        """What a note sent from here says it came from (feedback.save_feedback)."""
        return [f"- session: {self.session_id or '-'} ({getattr(self, 'session_title', None) or 'untitled'})",
                f"- folder: {self.cwd}", f"- Baude: {os.path.realpath(__file__)}"]

    def capture_areas(self):
        """The window's parts, by name, for the text a capture records."""
        return {self.sidebar: "sidebar", self.scroller: "chat", self.panel: "side panel",
                self.composer_box: "input box", self.status.get_parent(): "status bar", self.header_bar: "header",
                self.banner: "banner", self.kube: "Kubernetes", self.schema: "databases", self.lightbox: "image"}

    def restart_state(self):
        """What a restart brings back: the chat, where it was scrolled to, the window, and whatever's being written: the
        draft (its caret and attachments too), a Feedback note put aside, the Feedback tab's reply."""
        adj = self.scroller.get_vadjustment()
        fixes_page = self.tabs.get(("fixes",))
        return {
            "cwd": self.run_cwd, "session_id": self.session_id, "viewing": self.resume_pending,
            "cut": bool(self.busy and self.remote is None and self.proc is not None),  # (Baude's claude: cut here)
            "remote": self.remote.key if self.remote is not None else None,  # (a network session's server)
            "at_bottom": self.stick_bottom,
            "from_bottom": adj.get_upper() - adj.get_page_size() - adj.get_value(),
            "chunk": None if self.stick_bottom else self.view_chunk(),  # (open_at)
            "draft": widgets.typed_text(self.input), "caret": widgets.caret_of(self.input),
            "attachments": [a.state() for a in self.attachments],
            "feedback_draft": self.feedback_draft.state() if self.feedback_draft else None,
            "fixes": fixes_page.state() if fixes_page else None, "fixes_shown": self.tabs.current() is fixes_page,
            "model": self.model, "mode": self.mode, "cost": self.total_cost,
            "size": [self.get_width(), self.get_height()], "maximized": self.is_maximized(),
            "frame": widgets.window_frame(self),  # (macOS: the display it's on, where; put back by put_back)
            "panel": self.panel.get_visible(),
            "places": self.PLACES,  # (where the other chats were left: open_at)
        }

    def apply_restart_state(self, state):
        if state.get("cut"):  # (a Desktop chat's turn this restart cut short: said once it's drawn, show_cut)
            self.cut_pending = time.time()
        for sid, place in (state.get("places") or {}).items():
            self.PLACES.setdefault(sid, place)
        if state.get("size") and not state.get("maximized"):
            self.set_default_size(*state["size"])
        widgets.put_back(self, state.get("frame"), state.get("maximized"))  # (where it was, on macOS)
        if state.get("panel"):
            self.show_panel(True)
        if state.get("draft"):
            self.input.get_buffer().set_text(state["draft"])
            widgets.put_caret(self.input, state.get("caret"))
        for att in state.get("attachments") or ():
            self.add_attachment(Attachment.from_state(att))
        if state.get("feedback_draft"):
            self.feedback_draft = FeedbackDraft.from_state(state["feedback_draft"])
        if state.get("cost"):
            self.total_cost = max(self.total_cost, float(state["cost"]))
            self.update_status()
        if state.get("fixes"):
            self.open_fixes(select=bool(state.get("fixes_shown"))).restore(state["fixes"])
        if self.resume_pending and not state.get("viewing"):
            # baude itself was running this session: nobody else is writing it
            self.tail.last_written, self.tail.turn_open = 0.0, False
            self.update_banner()
        self.stick_bottom = bool(state.get("at_bottom", True))
        if not self.stick_bottom and not (self.history and state.get("chunk") and self.place_chunk(state["chunk"])):
            # hold the place (measured from the bottom) while the history lays out and images load
            self.restore_scroll = (float(state.get("from_bottom") or 0), time.monotonic() + 2.0)

    def restart_app(self):
        """☰ → Restart Baude: every window (BaudeApp.restart)."""
        self.get_application().restart()

    def idle(self):
        """Nothing a restart would cut short or take from under you (update.Updater): restart_blockers."""
        return not self.restart_blockers()

    def restart_blockers(self):
        """What a restart of Baude waits for in this window, said (the Update Available bar's tooltip), [] for none: a
        command of Baude's own claude's running (a Desktop chat's; a host's chats go on through a restart, commands and
        all), a side question's follow-up being written (that doesn't come back; what's typed elsewhere does:
        restart_state), a dialog or a capture open, and you at it (the window you're in, something typed, clicked or
        scrolled in it in the last minute). Not the model writing: a turn cut short there is resumed (show_cut)."""
        why = [f"running: {w}" for w in self.own_commands()]
        if any(sq.follow.get_text().strip() for sq in self.panel.side_questions()):
            why.append("a side question's reply being written")
        bar = getattr(self, "capture_bar", None)
        if self.get_visible_dialog() is not None or (bar is not None and bar.get_reveal_child()):
            why.append("a dialog open")
        if not update.quiet(self):
            why.append("you, using this window")
        return why

    def own_commands(self):
        """The commands Baude's own claudes are running for this window (a Desktop chat's, here or left mid-turn): what a
        restart of Baude would stop. A host's chats' aren't: they go on through it."""
        out = []
        if self.proc is not None and self.remote is None:
            out += [command_what(c.input) for c in self.tool_cards.values()
                    if c.base in COMMAND_TOOLS and c.state == "running"]
            out += [t.get("description") or "a background task" for t in self.running_tasks()
                    if t.get("task_type") != "local_agent"]
        for p in self.background.values():
            out += list((getattr(p, "commands", None) or {}).values())
        return out

    # ---- a turn cut short by a restart (this chat's host's, or this Baude's for a Desktop chat): said at the chat's
    # end, with Continue, till something's sent to it
    def cut_state(self):
        """When a turn of this chat's was cut short by a restart, or None."""
        if self.remote is not None:
            return (self.remote.sessions.get(self.session_id) or {}).get("cut")
        return getattr(self, "cut_pending", None)

    def show_cut(self, cut=None):
        """The line at the chat's end that says a restart cut its last turn short (cut: when, as cut_state; None, gone),
        put last again if it's already there (what's drawn after it, a chunk of the chat, is above it)."""
        row = getattr(self, "cut_row", None)
        if row is not None and row.get_parent() is not None:
            row.get_parent().remove(row)
        self.cut_row = None
        if not cut:
            return
        row = Gtk.Box(spacing=8, halign=Gtk.Align.START)
        row.add_css_class("cut-note")
        said = Gtk.Label(label="Cut short by a restart", xalign=0)
        said.add_css_class("dim-label")
        go = Gtk.Button(label="Continue", tooltip_text=f"Send “{CUT_CONTINUE}”")
        go.connect("clicked", lambda *_: self.continue_cut())
        row.append(said)
        row.append(go)
        self.transcript.append(row)
        self.cut_row = row

    def continue_cut(self):
        """Continue: the chat told to carry on, as you'd tell it (what's attached and typed left for what you send)."""
        self.cut_pending = None
        self.show_cut(None)
        atts, self.attachments = self.attachments, []
        if self.resume_pending:
            self.begin_resume()
        self.send_text(CUT_CONTINUE)
        self.attachments = atts + self.attachments
        self.refresh_attach_strip()

    # ---- helpers
    def short_cwd(self):
        tree = main_folder(self.run_cwd)[1]
        return pretty_path(self.cwd) + (f" (worktree {os.path.basename(tree)})" if tree else "")

    def reset_stream_state(self):
        self.blocks = {}         # stream index -> widget for current message
        self.tool_cards = {}     # tool_use_id -> ToolCard
        self.tool_parent = {}    # tool_use_id -> the subagent's call it ran inside (nested agents)
        self.task_lines = {}     # task id -> the line its ending got while its card wasn't drawn (task_note)
        self.streamed_ids = set()  # message ids we already rendered via stream events

    def add(self, widget, parent_tool=None):
        """Append to the chat, or to a subagent's transcript (side panel, else inside its card).
        Tool calls and the thinking between them fold into a ToolGroup."""
        if parent_tool and isinstance(widget, ToolCard):
            self.tool_parent[widget.tool_id] = parent_tool
        if parent_tool and (view := self.panel.agent_view(parent_tool)) is not None:
            target = view
        elif parent_tool in self.tool_cards:
            self.tool_cards[parent_tool].show_output()
            target = self.tool_cards[parent_tool].children
        else:
            target = self.render_target or self.transcript
        if isinstance(widget, ToolCard):
            return self.add_activity(target, widget)
        target.append(widget)
        return widget

    def add_activity(self, target, widget):
        last = target.get_last_child()
        if isinstance(last, ToolGroup):
            last.add_item(widget)
            return widget
        # the run of tool cards at the end of the container (narration between them splits it)
        run, child = [], last
        while isinstance(child, ToolCard):
            run.insert(0, child)
            child = child.get_prev_sibling()
        if isinstance(widget, ToolCard) and any(isinstance(w, ToolCard) for w in run):
            group = ToolGroup()
            for w in run:
                target.remove(w)
                group.add_item(w)
            group.add_item(widget)
            target.append(group)
            return widget
        target.append(widget)
        return widget

    def task_note(self, note):
        """The line a background task's ending gets: none while its card's in the chat, which says it ended itself
        (ToolCard.finish_task); else task_note_widget's, which links to its card if that's drawn later (card_task)."""
        task = self.tasks.get(note.get("task_id")) or {}
        if self.tool_cards.get(note.get("tool_use_id") or task.get("tool_use_id")) is not None:
            return None
        line = task_note_widget(note)
        if note.get("task_id"):
            self.task_lines[note["task_id"]] = line
        return line

    def card_task(self, card):
        """A card drawn after its background task's ending was (that's in a later part of the chat, drawn first): it
        says how the task ended, as a card that saw it end does, and the ending's line links back to it."""
        task = next((t for t in self.tasks.values() if t.get("tool_use_id") == card.tool_id), None)
        if task is None or task.get("status") in (None, "running", "interrupted"):
            return
        card.finish_task(task)
        if (line := self.task_lines.pop(task["task_id"], None)) is not None:
            line.link_card(card, self.jump_to_card)

    def jump_to_card(self, card):
        """Scroll the chat back to a card (opening the group it's folded into) and flash it."""
        p = card.get_parent()
        while p is not None and p is not self.transcript:
            if isinstance(p, ToolGroup):
                p.toggle.set_expanded(True)
            p = p.get_parent()
        if p is None:
            return
        self.stick_bottom = False
        card.add_css_class("flash")
        GLib.timeout_add(1400, lambda: card.remove_css_class("flash") and False)

        def scroll():
            ok, b = card.compute_bounds(self.transcript)
            if ok:
                adj = self.scroller.get_vadjustment()
                adj.set_value(max(0, b.get_y() - 48))
            return False
        GLib.timeout_add(160, scroll)  # after the group's slide-down has laid out

    def note(self, text, parent_tool=None):
        return self.add(sel_label(html.escape(text), css="sysnote"), parent_tool)

    def on_scrolled(self, _adj):
        # (the scrollbar or the keys moved it: the reader; ScrollGlide says so itself. Not GTK clamping it, the view
        # held, or the end followed: those aren't a move of the reader's, and neither waits for them)
        if self.thumb_held or time.monotonic() - self.keyed_at < 0.3:
            self.reader_moved()
        if self.fill_tick is None and (self.chunk_slots or self.fill_jobs):
            self.plan_fill()  # (an earlier chunk near the view now, or under it: drawn first)

    def on_scrollbar_event(self, controller, *_):
        event = controller.get_current_event()  # (its argument's None under PyGObject 3.58)
        kind = event.get_event_type() if event is not None else None
        if kind == Gdk.EventType.BUTTON_PRESS:
            self.thumb_held = True
        elif kind in (Gdk.EventType.BUTTON_RELEASE, Gdk.EventType.GRAB_BROKEN):
            self.thumb_held = False
        return False

    # ---- while the chat's scrolled, nothing in it is under the pointer. GTK looks again at what's under a still pointer
    # each frame the chat moves (a synthesized motion: a pick through every widget of the chat from the bottom up to the
    # pointer), hover goes from message to message as they pass under it, and each change restyled whole chunks: in a
    # long chat 25-60 ms a frame, 11-22 frames a second. Back STILL_MS after the last move, or at once when a glide or
    # a wheel's click comes to rest, or on a press; not while a selection's dragged (it finds its labels by a pick).
    STILL_MS = 150

    def reader_moved(self):
        self.moved_at = time.monotonic()
        Signal.moved()  # (Signal's lookups wait while a chat's scrolled)
        if self.text_selection.dragging:
            return
        if self.transcript.get_can_target():
            self.transcript.set_can_target(False)
        if self.still_timer is not None:
            GLib.source_remove(self.still_timer)
        self.still_timer = GLib.timeout_add(self.STILL_MS, self._still_timeout)

    def _still_timeout(self):
        self.still_timer = None
        self.chat_still()
        return False

    def chat_still(self):
        if self.still_timer is not None:
            GLib.source_remove(self.still_timer)
            self.still_timer = None
        if not self.transcript.get_can_target():
            self.transcript.set_can_target(True)
            # (GTK looks at what's under a still pointer again only after a layout: hover, the pointer's shape, and
            # where a click without a move between lands come back with the next frame)
            self.transcript.queue_allocate()
        if self.chunk_slots or self.fill_jobs:
            self.plan_fill()  # (the rest of the history drawn on, now it's still)

    def chat_moving(self):
        """Scrolled by the reader, and not still since (chat_still): what's drawn ahead waits for that."""
        return self.still_timer is not None or self.scroll_glide.gliding()

    stick_bottom = property(lambda self: self.follow.stick, lambda self, on: setattr(self.follow, "stick", on))

    def on_reader_scroll(self, *_):
        self.restore_scroll = None  # the reader is scrolling: a restart's saved place no longer applies
        return False

    def on_layout(self, _clock):
        """Right after each layout pass: what's being held in place first, else the newest content in view."""
        # (a zoom, or a restart's place put back: those hold the view themselves, this one would fight them)
        # (a chunk opened at, place_chunk: the view put in it; what's at its top is noted once it's laid out there)
        self.trim_slack()
        placed = self.place_at is not None and self.put_place()
        if placed:
            self.view_anchor = None
        held = True if placed else \
            self.hold_view_anchor() if self.zoom_job is None and self.restore_scroll is None else None
        if getattr(self, "keep_place", None) is not None:
            self.hold_keep_place(held)
        if self.zoom_job is not None:
            self.view_anchor = None
            self.hold_zoom_place()
            return
        if self.restore_scroll is not None:
            self.view_anchor = None
            from_bottom, until = self.restore_scroll
            adj = self.scroller.get_vadjustment()
            if time.monotonic() > until:
                self.restore_scroll = None
            else:
                self.follow.put(max(0.0, adj.get_upper() - adj.get_page_size() - from_bottom))
                # (following the end only if that's where it's held: GTK clamping the view while earlier parts are
                # drawn above it could have said otherwise, and the end would be jumped to once the hold was over)
                self.follow.stick = from_bottom <= 1
            return
        if self.above_changed:
            self.above_changed = False
            if self.stick_bottom:  # (an earlier chunk drawn or sized again: the end's kept at once, not glided to as
                self.follow.to_end()  # new content at the end is)
        self.toggle_settled()
        self.follow.laid_out()
        if held is not True:  # (moved this pass: what's in view is found once it's laid out where it's moved to)
            self.note_view_anchor()
        self.cull_chunks()

    # ---- history chunks far from the view taken out of what's drawn and picked: set_child_visible keeps them laid out
    # (nothing moves, bounds still hold) but unmapped, so GTK's picks and GSK's walk of every node it draws (each frame:
    # 13% of a long chat's frame) skip them, and their pictures give their pixels back. Back within CULL_NEAR screens,
    # out beyond CULL_FAR: one at the edge isn't shown and hidden over and over.
    CULL_NEAR, CULL_FAR = 2.0, 4.0  # screens

    def view_top(self):
        """The top of the view in the chat's coordinates, and the view's height; None if it isn't laid out."""
        page = self.scroller.get_height()
        ok, tb = self.transcript.compute_bounds(self.scroller)
        return (-tb.get_y(), page) if ok and page > 0 else (None, page)

    def cull_chunks(self):
        hist = getattr(self, "history_box", None)
        top, page = self.view_top()
        if hist is None or top is None:
            return
        at = (round(top * 4 / page), hist.get_height())  # (a quarter of a screen moved, or the chunks' height changed)
        if at == self.cull_at:
            return
        self.cull_at = at
        going, slot = set(), hist.get_first_child()
        while slot is not None:
            ok, b = slot.compute_bounds(self.transcript)
            if ok and b.get_height() > 0:
                away = max(top - (b.get_y() + b.get_height()), b.get_y() - (top + page))
                if away < self.CULL_NEAR * page and not slot.get_child_visible():
                    slot.set_child_visible(True)
                elif away > self.CULL_FAR * page and slot.get_child_visible():
                    going.add(slot)
            slot = slot.get_next_sibling()
        watch = getattr(self.scroller, "picture_watch", None)
        for pic in list(watch.pictures) if going and watch is not None else ():
            chunk = pic.get_parent()
            while chunk is not None and chunk.get_parent() is not hist:
                chunk = chunk.get_parent()
            if chunk in going:
                pic.release()  # (decoded again as it comes near: PictureWatch)
        for slot in going:
            slot.set_child_visible(False)

    def item_below(self, y):
        """The chat's first item (a message, card or group, inside history chunks too) whose bottom is below y, in the
        chat's coordinates, with its bounds: by their bounds, not a pick (a pick walks every widget of the chat below
        the point: 1-25 ms in a long one, and nothing at all while it's scrolled: reader_moved)."""
        hist, btn = getattr(self, "history_box", None), getattr(self, "earlier_btn", None)
        child = self.transcript.get_first_child()
        while child is not None:
            if child.get_visible() and child is not btn:
                ok, b = child.compute_bounds(self.transcript)
                if ok and b.get_y() + b.get_height() > y:
                    if child is not hist:
                        return child, b
                    slot = hist.get_first_child()
                    while slot is not None:
                        ok, sb = slot.compute_bounds(self.transcript)
                        if ok and sb.get_height() > 0 and sb.get_y() + sb.get_height() > y:
                            item = slot.get_first_child()
                            while item is not None:
                                if item.get_visible() and item is not getattr(slot, "spacer", None):
                                    ok, ib = item.compute_bounds(self.transcript)
                                    if ok and ib.get_y() + ib.get_height() > y:
                                        return item, ib
                                item = item.get_next_sibling()
                        slot = slot.get_next_sibling()
            child = child.get_next_sibling()
        return None, None

    # ---- the view held to the message at its top as what's above it changes height (a chunk of history drawn, a web
    # image arriving in place of its link, a card filling in): GTK keeps the scrolled distance, so all of that pushed
    # what's read down, or up. After each layout pass the first message in view is noted with where it is in the chat;
    # after the next, the view moves by as much as it has. Not while following the end, nor across a change of width
    # (a reflow: zoom_job and keep_place_through_restyle hold the view then).

    def chat_message_of(self, widget):
        """The message widget is in (a child of the chat, or of a history chunk): None for the chat's own boxes, the
        Show earlier button, or what's outside the chat (the scrollbar)."""
        hist = getattr(self, "history_box", None)
        while widget is not None:
            parent = widget.get_parent()
            if parent is self.transcript or (hist is not None and parent is not None and parent.get_parent() is hist):
                return None if widget is hist or widget is getattr(self, "earlier_btn", None) or \
                    widget is getattr(parent, "spacer", None) else widget
            widget = parent
        return None

    def note_view_anchor(self):
        self.view_anchor = None
        if self.stick_bottom or self.transcript.get_width() <= 0:
            return
        top, _page = self.view_top()
        msg, b = self.item_below(top + 1) if top is not None else (None, None)
        if msg is not None:
            self.view_anchor = (msg, b.get_y(), self.transcript.get_width())

    def hold_view_anchor(self):
        """The view moved by as much as the message noted at its top has (note_view_anchor): True if it was moved,
        False if it's where it was, None if there's nothing to hold to."""
        anchor = getattr(self, "view_anchor", None)
        if anchor is None:
            return None
        msg, y, width = anchor
        if self.stick_bottom or width != self.transcript.get_width() or not msg.is_ancestor(self.transcript):
            self.view_anchor = None
            return None
        ok, b = msg.compute_bounds(self.transcript)
        if not ok:
            self.view_anchor = None
            return None
        if abs(b.get_y() - y) < 0.5:
            return False
        self.hold_value(self.scroller.get_vadjustment().get_value() + b.get_y() - y)
        self.view_anchor = (msg, b.get_y(), width)  # (held to it till the view's laid out where it's been moved)
        return True

    def hold_toggle(self, toggle):
        """A toggle in the chat clicked (a card's Command or Output, a group's › 5 tool calls): it stays where it is on
        screen, what opens under it pushing what's below down (closing, pulling it up). The end isn't followed from
        then on, nor a restart's place kept: either would keep the bottom still and move the toggle. The message at
        the view's top holds the view (hold_view_anchor); scrolled back to the end, it's followed again, and so it is
        if it's at the end once the toggle's done sliding (closed again where it was opened: toggle_settled). Closed
        with less below the view than closes, the end would come up past the view's bottom and GTK would move the view
        down to it, the toggle with it: blank room is left under the last message instead (set_slack)."""
        if not toggle.button.is_ancestor(self.transcript):
            return
        self.follow.stop_glide()
        self.stick_bottom, self.restore_scroll, self.toggle_held = False, None, toggle
        adj = self.scroller.get_vadjustment()
        below = adj.get_upper() - adj.get_page_size() - adj.get_value()  # (what's below the view)
        if toggle.expanded and toggle.revealer is not None and toggle.revealer.get_height() > below + 0.5:
            self.set_slack(self.transcript.get_height() - below)

    def set_slack(self, height):
        """The chat kept at least height px tall (None: as tall as what's in it): what it's short of that is blank room
        under its last message, for the view to sit past the end (hold_toggle) till it can go (trim_slack)."""
        self.slack = None if height is None else (math.ceil(height), self.transcript.get_width())
        self.transcript.set_size_request(-1, -1 if height is None else self.slack[0])

    def trim_slack(self):
        """After each layout pass, the slack that's below the view goes: scrolled up from it, it's gone, and scrolled
        down again the view stops at what's left of it. Nothing on screen moves. It all goes once what comes into the
        chat has filled it (the chat taller than it's kept), and with a reflow (another width, a zoom). With the view
        at what's left of it, the end's in view and followed (FollowEnd decided before what was below went)."""
        if self.slack is None:
            return
        height, width = self.slack
        if self.transcript.get_height() > height or self.transcript.get_width() != width or self.zoom_job is not None:
            self.set_slack(None)
            return
        adj = self.scroller.get_vadjustment()
        below = adj.get_upper() - adj.get_page_size() - adj.get_value()
        if below >= 1:
            self.set_slack(height - below)
        elif self.toggle_held is None:  # (not while one opens into it: that's held, not followed)
            self.stick_bottom = True

    def toggle_settled(self):
        held = self.toggle_held
        here = held is not None and held.button.is_ancestor(self.transcript)  # (not: the chat's been switched)
        if held is None or here and held.sliding():
            return
        self.toggle_held = None
        adj = self.scroller.get_vadjustment()
        if here and adj.get_value() + adj.get_page_size() >= adj.get_upper() - 1:
            self.stick_bottom = True

    def hold_value(self, value):
        """The view put at value to hold what's in it still (not a move of the reader's: reader_moved isn't told)."""
        self.scroller.get_vadjustment().set_value(value)

    def set_busy(self, busy):
        self.busy = busy
        if not busy and self.proc is not None:
            self.turn_finished = True
        self.set_send_button(busy)
        self.update_status()
        self.update_session_state()

    def session_state(self):
        """working / waiting / done / inactive, for the sidebar's dot."""
        if self.resume_pending and self.remote is not None:  # (a network session viewed: as its server says)
            listed = self.remote.sessions.get(self.session_id) or {}
            return "inactive" if self.remote.state == "dead" else \
                {"working": "working", "waiting": "waiting"}.get(listed.get("state"), "inactive")
        if self.resume_pending:  # only viewing: what the program writing it is doing
            return self.tail.state()
        if self.waiting_cards:
            return "waiting"
        if self.busy or self.running_tasks():
            return "working"
        if getattr(self, "turn_finished", False) and self.proc is not None:
            return "done"
        return "inactive"

    def update_session_state(self):
        state = self.session_state()
        if hasattr(self, "sidebar"):
            self.sidebar.set_state(state)

    def set_send_button(self, busy):
        self.composer_box.set_busy(busy)  # (idle: a plain grey button; while working, a red stop button)

    # ---- text zoom
    def set_zoom(self, zoom):
        zoom = round(min(ZOOM_MAX, max(ZOOM_MIN, zoom)), 2)
        if zoom == self.zoom:
            return
        self.zoom = zoom
        save_settings(zoom=zoom)
        # a burst of Ctrl+ presses re-lays the chat out once; a held key or a long swipe still
        # shows a new size every 60 ms instead of only when it stops
        if not self.zoom_timer:
            self.zoom_timer = GLib.timeout_add(60, self.apply_zoom_keeping_place)

    def show_zoom_toast(self):
        # A toast coming or going restyles everything shown in the window (a long chat: half a
        # second), so it comes once the zoom has hidden what's far off, and a burst shares one.
        title = f"Chat zoom {fmt_scale(self.zoom)}"
        if self.zoom_toast:
            self.zoom_toast.set_title(title)
            return
        self.zoom_toast = Adw.Toast(title=title, timeout=1)
        self.zoom_toast.connect("dismissed", lambda *_: setattr(self, "zoom_toast", None))
        self.toasts.add_toast(self.zoom_toast)

    def apply_zoom(self):
        self.zoom_css.load_from_string(f".zoomable {{ font-size: {self.zoom:.2f}em; }}")

    def zoom_units(self):
        """The chat's top-level pieces (messages, cards, groups), looking inside history chunks."""
        child = self.transcript.get_first_child()
        while child is not None:
            if getattr(child, "is_history", False):
                chunk = child.get_first_child()
                while chunk is not None:
                    inner = chunk.get_first_child()
                    while inner is not None:
                        if inner is not getattr(chunk, "spacer", None):  # (a chunk's room, while it's drawn)
                            yield inner
                        inner = inner.get_next_sibling()
                    chunk = chunk.get_next_sibling()
            else:
                yield child
            child = child.get_next_sibling()

    def apply_zoom_keeping_place(self):
        self.zoom_timer = None
        self.keep_place_through_restyle()
        self.apply_zoom()
        self.show_zoom_toast()
        return False

    def keep_place_through_restyle(self):
        """Before the text's size changes (the zoom, or Settings → Size): without the slow full relayout or the jump.
        Re-laying out a long session at a new size takes seconds, so only what's near the view is laid out at once: the
        rest is hidden and comes back a few messages a frame, nearest first. Each frame, after GTK's layout and before
        anything is drawn, on_layout puts the message at the top of the view back where it was on screen (or keeps the
        view at the bottom, if it was there). Putting it back from a timer instead let frames through with the view
        off by a whole batch of messages."""
        self.cancel_keep_place()  # a stale "Show earlier" correction would fight the zoom's own
        self.follow.stop_glide()
        self.restore_scroll = None
        clock = self.get_frame_clock()
        if clock is None:  # not on screen: there's no place to keep
            return
        adj = self.scroller.get_vadjustment()
        top, page = adj.get_value(), adj.get_page_size()
        anchor, above, below = None, [], []
        for unit in self.zoom_units():
            if getattr(unit, "zoom_hidden", False):  # still hidden from the last zoom
                (below if anchor else above).append(unit)
                continue
            if not unit.get_visible():
                continue
            ok, b = unit.compute_bounds(self.transcript)
            if not ok:
                continue
            y0, y1 = b.get_y(), b.get_y() + b.get_height()
            if anchor is None and y1 > top:
                anchor = unit
            if y1 < top - 2 * page or y0 > top + 3 * page:
                unit.set_visible(False)
                unit.zoom_hidden = True
                (below if anchor else above).append(unit)
        below.reverse()  # both lists pop() the message nearest the view first
        hold = self.zoom_job
        if hold is None:
            hold = self.zoom_job = {"clock": clock}
            # the messages come back as the frame begins, before GTK styles what's shown in its tick callbacks (one
            # shown after them was styled a frame late: drawn at the old size, then the new); the place is noted
            # after them, so a touchpad's kinetic glide counts as the reader's
            hold["handlers"] = (clock.connect("before-paint", self.zoom_show),
                                clock.connect_after("update", self.zoom_frame))
            clock.begin_updating()
        hold.update(anchor=anchor, above=above, below=below, upper=None, calm=0, frames=0, shown=0)
        self.note_zoom_place()

    def note_zoom_place(self):
        """Where the reader is, as last drawn and scrolled since: the bottom, or how far the anchor
        is from the top of the view."""
        hold, adj = self.zoom_job, self.scroller.get_vadjustment()
        value = adj.get_value()
        hold["bottom"] = value + adj.get_page_size() >= adj.get_upper() - 1
        hold["offset"] = None
        if hold["anchor"] is not None:
            ok, b = hold["anchor"].compute_bounds(self.transcript)
            if ok:
                hold["offset"] = b.get_y() - value

    def zoom_show(self, _clock):
        """As each frame begins: show the next few hidden messages, styled at the new size in this frame."""
        hold = self.zoom_job
        if hold is None:
            return
        shown = 0
        while shown < 16 and (hold["above"] or hold["below"]):
            for side in (hold["below"], hold["above"]):
                if side:
                    unit = side.pop()
                    unit.set_visible(True)
                    unit.zoom_hidden = False
                    shown += 1
        hold["shown"] = shown

    def zoom_frame(self, clock):
        """Before each frame's layout: note the place afresh (so the reader can scroll meanwhile
        and isn't pulled back); the messages zoom_show brought back aren't laid out yet."""
        hold = self.zoom_job
        if hold is None:
            return
        self.note_zoom_place()
        if hold["shown"]:
            return
        # all shown: hold on until the height stops changing (code blocks follow the view's height,
        # which the composer's new text size changes, a frame later)
        upper = self.scroller.get_vadjustment().get_upper()
        hold["calm"] = hold["calm"] + 1 if upper == hold["upper"] else 0
        hold["upper"], hold["frames"] = upper, hold["frames"] + 1
        if hold["calm"] >= 3 or hold["frames"] > 60:
            self.zoom_job = None
            for handler in hold["handlers"]:
                clock.disconnect(handler)
            clock.end_updating()
            self.plan_fill()  # (the earlier chunks drawn on: they waited for the zoom)

    def hold_zoom_place(self):
        hold = self.zoom_job
        # (GTK clamps the view while the hidden messages are out, which on_scrolled took for the
        # reader going to the bottom: the view then followed new content down there afterwards)
        self.stick_bottom = hold["bottom"]
        if hold["bottom"]:
            self.follow.to_end()
        elif hold["offset"] is not None:
            ok, b = hold["anchor"].compute_bounds(self.transcript)
            if ok:
                self.follow.put(b.get_y() - hold["offset"])

    # ---- settings
    def open_settings(self):
        """Menu → Settings… (one at a time: the key again while it's open leaves it)."""
        if isinstance(self.get_visible_dialog(), SettingsDialog):
            return self.get_visible_dialog()
        dialog = SettingsDialog(self)
        dialog.present(self)
        return dialog

    # macOS: once a dialog has opened, the whole window drawn afresh (see Repaint), unless every frame already is
    REPAINT_AFTER_DIALOG = sys.platform == "darwin" and not FULL_REDRAWS

    def dialog_shown(self, _pspec):
        if self.REPAINT_AFTER_DIALOG and self.get_visible_dialog() is not None and self.repaint_wait is None:
            self.repaint_wait = [None, time.monotonic()]  # (the frame last seen, and since when this has waited)
            GLib.timeout_add(100, self.repaint_when_still)

    def repaint_when_still(self):
        """Every 100ms: once no frame has come since the last look (the dialog's opening done, which is when the tiles
        go), or after a second (something else animating), the window's flash."""
        clock = self.get_frame_clock()
        if clock is None:  # (closed meanwhile)
            self.repaint_wait = None
            return False
        frame = clock.get_frame_counter()
        if frame != self.repaint_wait[0] and time.monotonic() - self.repaint_wait[1] < 1.0:
            self.repaint_wait[0] = frame
            return True
        self.repaint_wait = None
        self.repaint.flash()
        return False

    def set_zoom_default(self, zoom):
        """Settings → Size → Chat zoom: the zoom Ctrl+0 (⌘0) goes back to; the chat goes to it now, to show it."""
        self.zoom_default = round(min(ZOOM_MAX, max(ZOOM_MIN, zoom)), 2)
        save_settings(zoom_default=self.zoom_default)
        self.set_zoom(self.zoom_default)

    scale_timer = None  # (one for the display, as the sizes are)

    def set_scale(self, key, size):
        """Settings → Size → Text or Icons ("text_scale", "icon_scale"): kept, and every window at it a moment after the
        last change (the text's restyles them all: once for a few quick clicks)."""
        save_settings(**{key: round(min(SCALE_MAX, max(SCALE_MIN, size)), 2)})
        if BaudeWindow.scale_timer:
            GLib.source_remove(BaudeWindow.scale_timer)
        BaudeWindow.scale_timer = GLib.timeout_add(150, BaudeWindow.scales_changed, self.get_display())

    @staticmethod
    def scales_changed(display):
        BaudeWindow.scale_timer = None
        for win in Gtk.Window.list_toplevels():  # (each chat keeps its place, as through a zoom)
            if isinstance(win, BaudeWindow) and win.get_display() == display:
                win.keep_place_through_restyle()
        apply_scales(display)
        return False

    # ---- full width
    def apply_full_width(self, on):
        # Adw.Clamp can't be switched off, so "off" is just a limit wider than any screen
        self.clamp.set_maximum_size(100_000 if on else 860)
        self.clamp.set_tightening_threshold(100_000 if on else 600)

    def on_fullwidth_toggled(self, action, value):
        action.set_state(value)
        self.apply_full_width(value.get_boolean())
        save_settings(full_width=value.get_boolean())

    # ---- control requests with replies
    def request(self, req, callback, refused=None):
        """callback(response) when claude does what's asked; refused(error) when it says no (with no refused, a no
        goes unsaid)."""
        if self.proc:
            self.pending[self.proc.control(req)] = (callback, refused)

    def refresh_context(self):
        self.request({"subtype": "get_context_usage"}, self.show_context)

    CONTEXT_EVERY_MS = 1000

    def refresh_context_soon(self):
        """The meter asked again a moment after each step of a turn (a reply, a tool's result), at most once a second:
        asked only as a turn ended, a small model's 32k filled through a turn of tool calls with the bar standing
        still, then jumped (a large window hid it)."""
        if getattr(self, "context_soon", None) is None:
            self.context_soon = GLib.timeout_add(self.CONTEXT_EVERY_MS, self._context_now)

    def _context_now(self):
        self.context_soon = None
        if self.proc:
            self.refresh_context()
        return False

    def show_context(self, usage):
        self.ctx_box.set_usage(usage)

    def refresh_effort(self):
        self.request({"subtype": "get_settings"},
                     lambda r: self.sync_effort((r.get("applied") or {}).get("effort")))

    def on_ctrl_scroll(self, ctl, _dx, dy):
        if self.schema.on_top():  # (it covers the chat: its diagram zooms itself, about the pointer)
            return False
        if self.zoom_code_scroll(ctl, dy):  # (over the code: its own size)
            return True
        if not ctl.get_current_event_state() & Gdk.ModifierType.CONTROL_MASK or dy == 0:
            return False
        # a wheel click is a step; a touchpad sends a stream of small deltas (pixels on Wayland)
        # that add up to steps, or each would be a step and the size would race to the limit
        per_step = 1.0 if ctl.get_unit() == Gdk.ScrollUnit.WHEEL else 60.0
        if self.zoom_wheel * dy < 0:  # turned around
            self.zoom_wheel = 0.0
        self.zoom_wheel += dy / per_step
        steps = int(round(self.zoom_wheel, 6))  # (ten 0.1s add up to 0.999…)
        if steps:
            self.zoom_wheel -= steps
            self.set_zoom(self.zoom - steps * ZOOM_STEP)
        return True

    # ---- attachments
    def add_attachment(self, att):
        self.attachments.append(att)
        self.refresh_attach_strip()

    def remove_attachment(self, att):
        self.attachments.remove(att)
        self.refresh_attach_strip()

    def refresh_attach_strip(self):
        self.draft_changed()  # (an attachment begins a chat too)
        while (c := self.attach_row.get_first_child()) is not None:
            self.attach_row.remove(c)
            discard(c)
        for att in self.attachments:
            self.attach_row.append(attachment_widget(att, 48, self.remove_attachment))
        self.attach_strip.set_visible(bool(self.attachments))

    def attach_paths(self, paths):
        for path in paths:
            if path and os.path.isfile(path):
                self.add_attachment(Attachment.from_path(path))
            elif path:
                self.note(f"can't attach {path}: not a file")

    def attach_texture(self, texture):
        try:
            self.add_attachment(Attachment.from_texture(texture))
        except (GLib.Error, ValueError) as e:
            self.note(f"can't attach image: {e}")

    def on_pick_attachments(self, *_):
        dlg = Gtk.FileDialog(title="Attach files", modal=True)
        filters = Gio.ListStore.new(Gtk.FileFilter)
        for name, mimes in (("All files", None),
                            ("Images & PDFs", ["image/*", "application/pdf"])):
            f = Gtk.FileFilter(name=name)
            for m in mimes or []:
                f.add_mime_type(m)
            if mimes is None:
                f.add_pattern("*")
            filters.append(f)
        dlg.set_filters(filters)

        def done(d, res):
            try:
                files = d.open_multiple_finish(res)
            except GLib.Error:
                return
            self.attach_paths([files.get_item(i).get_path() for i in range(files.get_n_items())])
        dlg.open_multiple(self, None, done)

    def on_drop(self, _target, value, _x, _y):
        if self.read_only:
            return False  # (nothing to send them with)
        if isinstance(value, Gdk.FileList):
            self.attach_paths([f.get_path() for f in value.get_files()])
        elif isinstance(value, Gdk.Texture):
            self.attach_texture(value)
        else:
            return False
        self.input.grab_focus()
        return True

    def paste_attachment(self):
        """Ctrl+V: attach copied files or a copied image; returns False to let text paste happen."""
        clip = self.input.get_clipboard()
        formats = clip.get_formats()
        if clipboard_has(formats, Gdk.FileList, ("text/uri-list", "x-special/gnome-copied-files")):
            def got_files(c, res):
                try:
                    self.attach_paths([f.get_path() for f in c.read_value_finish(res).get_files()])
                except GLib.Error as e:
                    self.note(f"paste failed: {e.message}")
            clip.read_value_async(Gdk.FileList, GLib.PRIORITY_DEFAULT, None, got_files)
            return True
        has_text = clipboard_has(formats, GObject.TYPE_STRING, ("text/plain",))
        if clipboard_has(formats, Gdk.Texture, ("image/",)) and not has_text:
            def got_texture(c, res):
                try:
                    self.attach_texture(c.read_texture_finish(res))
                except GLib.Error as e:
                    self.note(f"paste failed: {e.message}")
            clip.read_texture_async(None, got_texture)
            return True
        return False

    def update_status(self, extra=None):
        if extra is not None:
            self.status_extra = extra
        self.working_dots.set_working(self.busy)
        self.status.set_text(f"{self.short_cwd()}   ${self.total_cost:.4f}   "
                             f"{getattr(self, 'status_extra', '')}")
        self.status.set_tooltip_text(self.run_cwd)

    # ---- process lifecycle
    def runs_here(self):
        """Is the chat's claude this process's own (ClaudeProc)? A Claude Desktop chat's (desktop_chat, however it's
        opened: is_desktop); and every chat's while This Mac's host process is older than Baude (it'd take adopt and
        draft for a new conversation: check_host_version offers to restart it). Else every chat of Baude's, new or on
        this machine already, runs in the host process (take_up), as a host's session does, so a restart or an update
        of Baude's never stops it."""
        return self.remote is None and (self.desktop_chat or is_desktop(self.session_id) or host_old())

    def start_proc(self):
        if self.remote is not None:
            return self.start_remote_proc()
        if not self.runs_here() and self.session_id is None and not self.resume_pending:
            return self.start_draft()  # (a new chat: in the host process from the start)
        if not self.runs_here():  # (one on this machine already: nothing runs till its first message: take_up)
            self.proc = None
            if local_host() is None:  # (started now, so it's there when it's wanted: its commands and models meanwhile)
                self.local_server(lambda _r: self.proc is None and self.remote is None and self.apply_host_init(),
                                  quiet=True)
            self.apply_host_init()
            self.update_status()
            return
        try:
            # while a session is only being viewed, claude runs fresh (it supplies models and commands
            # but writes nothing); it resumes the session when the first message is sent
            resume = None if self.resume_pending else self.session_id
            # (how what runs the model is started: baudelib.models. Apple's model: apple-claude, Manual, no effort, a
            # chat of its own, never carried on; a model on this Mac: pi-claude, Manual, its thinking level)
            kind = modelslib.program(self.model)
            effort = (self.local_effort if kind["name"] == "pi" else self.effort) if self.effort_dd.get_sensitive() else None
            run = modelslib.launch(self.model, self.mode, effort, resume if kind["resumes"] else None, resumed=True)
            if run["missing"]:
                self.note("Apple Foundation Model: apple-claude isn't built yet (swift build -c release, in apple-claude/)")
                self.proc = None
                self.update_status()
                return
            self.proc = ClaudeProc(self.on_msg, self.on_exit, self.run_cwd, self.model, run["mode"], run["resume"],
                                   run["effort"], extra=run["extra"], exe=run["exe"])
            self.proc.resumed = resume
            self.pending.clear()
        except OSError as e:
            self.note(f"failed to start claude: {e}")
            self.proc = None
        self.update_status()

    def start_remote_proc(self):
        """A network session's claude, on its server (RemoteProc): while it's only viewed, what it asks and its models
        and commands (its transcript, fetched as it grows, is the rest: sync_remote); once you write in it, all it
        says, as it says it. Nothing if its server's gone."""
        r = self.remote
        self.proc, self.remote_tried = None, time.monotonic()
        if r.state == "alive":
            try:
                self.proc = RemoteProc(self.on_msg, self.on_exit, r.reach_where(), self.session_id,
                                       live=not self.resume_pending,  # (this machine's: as Baude's own claude would
                                       env=claudelib.claude_env() if r.mine else None,  # have had it)
                                       always_allow=sorted(self.always_allow))
            except OSError as e:
                self.note(f"couldn't reach {remote_name(r)}: {e}")
        if self.proc is not None:
            self.proc.mode = self.proc.listing.get("mode")
            self.proc.cost_seen = self.total_cost  # (its claude counts on from what the transcript says was spent)
            self.pending.clear()
            for msg in list(self.proc.asks.values()):  # (what it's waiting on an answer to)
                self.h_control_request(msg)
        self.update_status()
        self.update_banner()

    def side_question_proc(self, on_msg, on_exit):
        """(proc, forked) for a side question in a chat of Baude's: a session of the host process's, forked from the
        chat there (taken up first, if it isn't yet), that keeps nothing (--no-session-persistence), is listed nowhere,
        and goes when it's closed; a fresh one for a chat with nothing saved yet. (None, False) if it can't be had
        (an older host process: said)."""
        r = self.remote or local_host()
        if self.remote is None and r is not None and self.session_id and find_session(self.session_id):
            reply = r.call({"op": "start", "adopt": self.session_id, "cwd": self.run_cwd, "model": self.model,
                            "mode": self.mode, "group": "", "env": claudelib.claude_env()}, timeout=10)
            if reply.get("op") != "started":  # (the host's to fork: taken up, the chat here still only viewed)
                self.note(f"the side question couldn't be asked: {reply.get('error')}")
                return None, False
        if r is None or (r.info.get("version") or 1) < 2:
            self.note(f"{remote_name(r) if r else THIS_MACHINE}'s host process can't ask a side question: restart it")
            return None, False
        params = {"op": "start", "ephemeral": True, "mode": "plan", "model": self.model, "prompt": ""}
        # (the chat's, forked, if it's the host's: a fresh, context-free one, if nothing's written in it yet, the host
        # says: started's forked)
        params.update({"fork_from": self.session_id} if self.session_id and (self.remote is not None or
                                                                              find_session(self.session_id)) else
                      {"cwd": self.run_cwd if r.mine else "~"})
        reply = r.call(params, timeout=10)
        if reply.get("op") != "started":
            self.note(f"the side question couldn't be asked: {reply.get('error')}")
            return None, False
        forked = bool(reply.get("forked"))
        try:
            return RemoteProc(on_msg, on_exit, r.reach_where(), reply["session"]["id"], live=True,
                              env=claudelib.claude_env() if r.mine else None), forked
        except OSError as e:
            self.note(f"the side question couldn't be asked: {e}")
            return None, False

    def start_draft(self):
        """A new chat, in the host process from the start, as a draft there (listed and kept once it's written in, gone
        if it never is), its own claude giving it its commands and models, as one of Baude's own did; once This Mac's
        host process is there, if it isn't yet (started for it)."""
        self.proc = None
        r = local_host()
        if r is None:
            self.draft_wanted = wanted = object()
            self.local_server(lambda _r: getattr(self, "draft_wanted", None) is wanted and self.remote is None
                              and self.session_id is None and not self.resume_pending and self.start_draft(), quiet=True)
            self.update_status()
            return
        self.draft_wanted = None
        effort = (self.local_effort if model_program(self.model) == "pi" else self.effort) \
            if self.effort_dd.get_sensitive() else None
        reply = r.call({"op": "start", "draft": True, "prompt": "", "cwd": self.run_cwd, "model": self.model,
                        "effort": effort, "mode": self.mode, "env": claudelib.claude_env(), "group": ""}, timeout=10)
        if reply.get("op") != "started":
            self.note(f"{THIS_MACHINE}'s host process didn't start a chat: {reply.get('error')}")
            self.update_status()
            return
        self.remote, self.session_id, self.desktop_chat = r, reply["session"]["id"], False
        r.sessions[self.session_id] = reply["session"]  # (listed before it next says what it has)
        self.resume_pending, self.view_path = False, None
        MarkdownView.base_dir = self.run_cwd
        self.start_remote_proc()

    def apply_host_init(self):
        """A chat of Baude's with no claude yet (a new one, or one the host process hasn't taken up): its commands and
        models as This Mac's host process last had them from a claude (its info's init), till its own say."""
        r = local_host()
        init = (r.info.get("init") if r is not None else None) or {}
        if init:
            self.completer.set_commands(init.get("commands") or [])
            self.sync_models(init.get("models"))

    def take_up(self):
        """A chat of Baude's on this machine, to be written in: made the host process's (start: a new one; adopt: one
        claude has already, a terminal's or one Baude ran itself before, by its own id), and talked to through it from
        then on, as a host's session is. True if it is; False if This Mac's host process isn't there yet; None if it
        wouldn't take it up (said)."""
        r = local_host()
        if r is None:
            return False
        effort = (self.local_effort if model_program(self.model) == "pi" else self.effort) \
            if self.effort_dd.get_sensitive() else None
        params = {"op": "start", "cwd": self.run_cwd, "model": self.model, "effort": effort, "mode": self.mode,
                  "env": claudelib.claude_env(), "group": ""}  # (Baude's own chats: in no group)
        if self.session_id and find_session(self.session_id):
            params["adopt"] = self.session_id
        else:
            params["prompt"] = ""
        reply = r.call(params, timeout=10)  # (on the main loop: this machine's socket, an answer in a moment)
        if reply.get("op") != "started":
            self.note(f"{THIS_MACHINE}'s host process didn't take this chat up: {reply.get('error')}")
            return None
        listed = reply["session"]
        r.sessions[listed["id"]] = listed  # (listed before it next says what it has)
        self.remote, self.session_id, self.desktop_chat = r, listed["id"], False
        self.resume_pending, self.view_path = False, None
        self.tail.stop()
        self.start_remote_proc()
        self.sidebar.fill()
        return True

    def restart_proc(self, resume=True):
        if self.proc:
            p, self.proc = self.proc, None
            p.on_exit = lambda *_: None
            p.kill()
            self.remember_own_writes()
        if not resume:
            self.session_id, self.desktop_chat = None, False  # (a new conversation: Baude's, the host process's)
            if self.remote is not None:  # (a new conversation: the host process's draft, from start_proc)
                self.remote, self.resume_pending, self.view_path = None, False, None
                self.tail.stop()
                MarkdownView.base_dir = self.run_cwd
                self.update_banner()
            self.reset_stream_state()
        else:
            # the same conversation carries on: keep tool_use_id -> card, so results, task endings
            # and subagent messages that arrive later still find their cards (only the half-drawn
            # stream of the old process is dropped)
            self.blocks, self.streamed_ids = {}, set()
        for waiting in list(self.waiting_cards):  # (the claude that asked is gone: nothing to answer. A copy: answered()
            # takes the card out of waiting_cards as it goes)
            if isinstance(waiting, ToolCard):
                waiting.answered()
        self.waiting_cards.clear()
        self.turn_finished = False
        self.set_busy(False)
        self.start_proc()

    # ---- a Claude Desktop chat left mid-turn (another opened, or a new one started): Baude's own claude, here, which
    # goes on with the turn, not killed (every other chat's is the host process's, which goes on there whoever's
    # attached: detach_proc only lets go of it),
    # writing its transcript as it goes, and is this window's again when the chat's opened (attach_proc), in this
    # window or another. Meanwhile a permission it asks for waits, its chat's dot saying so (Sidebar.file_state), and
    # is asked when the chat's opened. Once its turn's over it's let go; it goes with the window.
    def detach_proc(self):
        if self.remote is not None:  # (a host's session: its turn goes on there, attached or not: let go of here)
            p, self.proc = self.proc, None
            p.on_msg, p.on_exit = (lambda *_: False), (lambda *_: None)
            p.kill()
            self.waiting_cards.clear()
            self.set_busy(False)
            return
        p, self.proc = self.proc, None  # (a Claude Desktop chat's claude, Baude's own: always one with an id)
        p.owner, p.key, p.mode, p.model = self, self.session_id or f"new-{id(p)}", self.mode, self.model
        p.path = (find_session(self.session_id) or {}).get("path") if self.session_id else None
        p.on_msg, p.on_exit = self.on_background_msg, lambda _code, p=p: self.let_go_background(p)
        p.commands = {f"c{i}": w for i, w in enumerate(self.own_commands())}  # (running as it's left: track_commands)
        self.background[p.key] = p
        self.waiting_cards.clear()
        self.set_busy(False)

    def on_background_msg(self, proc, msg):
        t, req = msg.get("type"), msg.get("request") or {}
        track_commands(proc.commands, msg)
        if t != "stream_event":
            own_writes(proc)  # (what it writes to its transcript meanwhile is Baude's, not another program's)
        if t == "control_request" and req.get("subtype") != "can_use_tool":
            proc.send({"type": "control_response", "response": {
                "subtype": "error", "request_id": msg["request_id"], "error": f"baude: unsupported {req.get('subtype')}"}})
        elif t == "control_request":
            if req.get("tool_name") in self.always_allow and model_program(proc.model) == "claude":
                proc.respond(msg["request_id"], {"behavior": "allow", "updatedInput": req.get("input", {})})
            else:
                proc.asks[msg["request_id"]] = msg
                self.sidebar.restate()
        elif t == "result":
            self.let_go_background(proc)
        return False

    def let_go_background(self, proc):
        """A chat's claude left mid-turn, its turn over (or the window closing): stopped, its chat's dot back to its
        transcript's."""
        if self.background.get(getattr(proc, "key", None)) is proc:
            del self.background[proc.key]
        proc.on_msg, proc.on_exit = (lambda *_: False), (lambda *_: None)
        proc.kill()
        own_writes(proc, exiting=True)
        self.sidebar.restate()

    def take_background(self, session_id):
        """The claude still at a turn in a chat left mid-turn, from whichever window left it: None if there's none."""
        if (p := background_proc(session_id)) is not None:
            p.owner.background.pop(p.key, None)
            p.owner.sidebar.restate()
        return p

    def attach_proc(self, p):
        """A chat's claude, left mid-turn (detach_proc), this window's again: what it did meanwhile is in the history
        just drawn, what it does next comes as it's done, and what it asked meanwhile is asked here."""
        if self.proc:
            old, self.proc = self.proc, None
            old.on_exit = lambda *_: None
            old.kill()
        self.resume_pending, self.view_path, self.resumed_note = False, None, False
        self.tail.stop()
        self.update_banner()
        p.on_msg, p.on_exit = self.on_msg, self.on_exit
        self.proc = p
        self.pending.clear()
        self.blocks, self.streamed_ids = {}, set()
        self.waiting_cards.clear()
        self.turn_finished = False
        if p.mode in self.mode_dd.values:  # (the mode it's running in, not the read-only one a chat opens in)
            self.mode = p.mode
            self.mode_dd.select(p.mode)
        self.set_busy(True)
        for msg in list(p.asks.values()):
            self.h_control_request(msg)

    def clear_transcript(self, closing=False):
        self.remember_place()  # (to open it there again)
        self.restore_scroll = None
        self.chat_sent, self.listed_begun = False, None  # (another chat: nothing sent in it yet)
        while (c := self.transcript.get_first_child()) is not None:
            self.transcript.remove(c)
            discard(c)
        self.cut_row = self.cut_pending = None  # (the next chat's own: cut_state)
        self.set_slack(None)  # (the next chat's no room left under it)
        self.toggle_held = None
        self.total_cost = 0.0
        self.ctx_box.reset()
        self.show_title(None)
        self.tasks.clear()
        self.sub_tails.clear()
        self.artifacts.clear()
        if not closing:  # (they're every window's: one closing leaves the others theirs)
            ARTIFACT_TITLES.clear()
        self.ref_surfaces.clear()
        self.reply_views.clear()
        self.orphan_results.clear()  # (results whose calls the chat before never drew: output, images and all)
        self.history, self.chunk_slots = None, None
        self.drop_fills()
        self.panel.reset()
        self.update_tasks_indicator()

    # ---- swapping the chat, with Claude's fade: the chat goes lighter, swaps, and the new one comes back
    def switch_to(self, action):
        """Swap the chat (another session, a new one, another folder) after a short fade, so the swap is seen.
        Clicks while it's faded (or a slow session still loading) don't start the fade again: the chat stays
        faded and the last one wins (Fade.swap)."""
        self.chat_fade.swap(action)

    def new_session(self, run_in=None):
        """A new chat in this folder; run_in: the folder claude runs in, if not this one (a worktree picked with
        Open folder…). A worktree's chat's folder is its repository, so a new one from there starts in that."""
        self.opening_remote = None  # (a network session still being fetched: not opened after all)
        run_in = self.norm_dir(run_in) if run_in else self.cwd if os.path.isdir(self.cwd) else self.run_cwd
        if run_in != self.run_cwd:
            self.switching_chat = True  # (the new chat's own switches come below)
            self.set_cwd(run_in)
        self.remember_own_writes()
        if self.busy and self.proc is not None:
            self.detach_proc()  # (the turn goes on: detach_proc)
        self.resume_pending, self.view_path, self.remote, self.desktop_chat = False, None, None, False
        MarkdownView.base_dir = self.run_cwd  # (a network session's folder is its server's: open_remote)
        self.tail.stop()
        self.update_banner()
        self.clear_transcript()
        self.read_only_mode()
        self.restart_proc(resume=False)
        self.signal_pending, self.switching_chat = None, False
        self.apply_chat_signal()  # (a new chat: the defaults for new chats)
        self.apply_read_only()
        self.sidebar.fill()
        self.panel.git_page.chat_changed()  # (its own Git page, in this folder too)

    # ---- read-only sessions (☰ → Read-only session): no text box, for a chat you only watch (tailing one with
    # Signal on, say); the status line, with the context it uses, stays. Remembered per session. A session another
    # program has (update_banner) is read-only too while it does, and has no text box either: show_composer
    def read_only_ids(self):
        return [i for i in load_settings().get("read_only_sessions") or [] if isinstance(i, str)]

    def set_read_only(self, on):
        """Make this session read-only, or not (and remember it for the session, once it has an id)."""
        if self.session_id:
            ids = [i for i in self.read_only_ids() if i != self.session_id] + ([self.session_id] if on else [])
            save_settings(read_only_sessions=ids or None)
        self.apply_read_only(on)

    def apply_read_only(self, on=None):
        """Show or hide the text box as the session asks (on: as given, None: as remembered for it)."""
        if on is None:
            on = bool(self.session_id) and self.session_id in self.read_only_ids()
        self.read_only = on
        if (action := self.lookup_action("readonly")) is not None:
            action.set_state(GLib.Variant.new_boolean(on))
        self.show_composer()

    def show_composer(self):
        """The text box, there unless the session can't be written to: read-only by choice (☰ → Read-only session),
        or while another program has it (update_banner). Read-only is no text box at all, not a greyed-out one."""
        hidden = self.read_only or self.locked
        self.composer.set_visible(not hidden)
        if hidden and self.input.has_focus():
            self.scroller.grab_focus()

    def on_exit(self, code):
        if self.remote is not None and code == "deleted":  # (deleted on its host, by another head: a new chat)
            self.proc = None
            self.set_busy(False)
            toast(self.sidebar, f"That session was deleted on {remote_name(self.remote)}")
            self.new_session()
            return
        if self.remote is not None:
            self.note(f"{remote_name(self.remote)}: " + ("the connection's gone" if code == -1 else
                                                         f"claude exited ({code})") + ". Send a message to carry on.")
            self.proc = None
            self.set_busy(False)
            self.remote_view()
            return
        self.note(f"claude exited ({code}). Send a message to restart / resume.")
        self.proc = None
        self.set_busy(False)

    # ---- UI events
    def on_key(self, _ctl, keyval, _code, state):
        if self.completer.handle_key(keyval, state) or self.refs.handle_key(keyval, state):
            return True
        if widgets.leave_wrapped(self.input.get_buffer(), keyval, state):
            return True  # (just formatted, and Left or Right: outside its marks)
        if keyval in (Gdk.KEY_Up, Gdk.KEY_Down) and state & Gdk.ModifierType.ALT_MASK and \
                not state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.META_MASK | Gdk.ModifierType.SHIFT_MASK):
            return self.composer_md.move_item(-1 if keyval == Gdk.KEY_Up else 1)  # (a list item, past its neighbour)
        if not state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.META_MASK) and \
                widgets.wrap_selection(self.input.get_buffer(), chr(Gdk.keyval_to_unicode(keyval) or 0)):
            return True  # (a selection, and * ` or /: bold, code, italic)
        if keyval in (Gdk.KEY_v, Gdk.KEY_V) and state & Gdk.ModifierType.CONTROL_MASK:
            return self.paste_attachment()
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            if state & Gdk.ModifierType.CONTROL_MASK:  # Ctrl+Enter always sends
                self.on_send()
                return True
            if state & Gdk.ModifierType.SHIFT_MASK or self.composer_md.code_state()[0]:
                return self.composer_md.newline()  # False: the TextView inserts a plain newline
            self.on_send()
            return True
        if keyval == Gdk.KEY_Escape and self.busy:
            self.interrupt()
            return True
        return False

    def on_send(self):
        buf = self.input.get_buffer()
        typed = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True).strip()
        if (typed in ("/login", "/logout") or typed.startswith("/login ")) and self.remote is not None \
                and not self.remote.local:  # (signing in is a machine's: another's claude signs in there)
            buf.set_text("")
            self.completer.hide()
            self.note(f"{remote_name(self.remote)}'s claude signs in there: run claude auth login on it")
            return
        if typed == "/btw" or typed.startswith("/btw "):  # even mid-turn: it doesn't interrupt
            question = typed[4:].strip()
            if question:
                buf.set_text("")
                self.completer.hide()
                self.ask_side_question(question)
            return
        if typed in ("/login", "/logout") or typed.startswith("/login "):  # (claude auth: even mid-turn, too)
            buf.set_text("")
            self.completer.hide()
            if typed == "/logout":
                self.sign_out()
            else:
                try:
                    self.sign_in(shlex.split(typed[len("/login"):]))
                except ValueError as e:  # (a quote left open)
                    self.note(f"/login: {e}")
            return
        if self.busy:
            self.interrupt()
            return
        if self.live_elsewhere():  # the input is off meanwhile; this is just the Enter key
            return
        buf = self.input.get_buffer()
        text = ComposerMarkdown.to_markdown(
            buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True)).strip()
        if (text or self.attachments) and self.resume_pending:
            self.begin_resume()
        if not text and not self.attachments:
            return
        # (sent: still listed in the sidebar once the composer's emptied, before the chat's first saved)
        self.chat_sent = getattr(self, "chat_sent", False) or not (text in ("/clear", "/new") or text == "/cd" or
                                                                   text.startswith("/cd "))
        buf.set_text("")
        self.completer.hide()
        if text in ("/clear", "/new"):
            self.switch_to(self.new_session)
            return
        if text == "/cd" or text.startswith("/cd "):
            self.change_cwd(text[3:].strip() or "~")
            return
        self.send_text(text)

    def send_text(self, text):
        """(on_send's) What was written, and what's attached, sent: to the host process for a chat of Baude's, taken up
        by it first (take_up; once it's started, if it isn't yet), or to the chat's claude here (a Desktop chat's)."""
        self.cut_pending = None  # (written to: no longer a turn cut short; its host forgets its own as it's sent)
        self.show_cut(None)
        buf = self.input.get_buffer()
        if self.proc is None and self.remote is None and not self.runs_here():
            took = self.take_up()
            if took is False:  # (This Mac's host process isn't there yet: started, and this sent once it is, from the
                buf.set_text(text)  # box it's kept in meanwhile, if it's still this chat's and still what's written)
                self.note(f"Starting {THIS_MACHINE}'s host process…")
                sid = self.session_id

                def ready(_r):
                    if self.session_id == sid and self.remote is None and widgets.typed_text(self.input).strip() == text:
                        buf.set_text("")
                        self.send_text(text)
                self.local_server(ready)
                return
            if took is None:
                buf.set_text(text)
                return
        if self.proc is None:
            self.start_proc()
        if self.proc is None:  # (it couldn't be started, or its server isn't there: what was written stays)
            buf.set_text(text)
            if self.remote is not None:
                self.remote_view()
            return
        atts, self.attachments = self.attachments, []
        self.refresh_attach_strip()
        self.add(self.user_bubble(text, atts))
        self.stick_bottom, self.restore_scroll = True, None  # (what you sent in view, whatever place was held)
        self.proc.user(self.user_content(text, atts))
        self.set_busy(True)

    def chat_begun(self):
        """Whether the open chat is something yet, so the sidebar lists it before it's saved: something typed or
        attached, a message sent, a title given. A blank new chat isn't listed."""
        return bool(getattr(self, "chat_sent", False) or getattr(self, "session_title", None) or self.attachments or
                    self.input.get_buffer().get_property("text").strip())

    def draft_changed(self):
        """The composer's text changed: the sidebar lists the open chat once it's begun, and not while it's blank."""
        if getattr(self, "sidebar", None) is not None and (begun := self.chat_begun()) != self.listed_begun:
            self.listed_begun = begun
            self.sidebar.fill()

    def ask_side_question(self, question):
        sq = SideQuestion(self, question)
        self.panel.add_side_question(sq)
        self.show_panel(True, "btw")
        return sq

    @staticmethod
    def user_content(text, atts):
        """Plain text when there is nothing attached; otherwise blocks, media first."""
        if not atts:
            return text
        blocks = [b for a in atts if (b := a.block())]
        paths = [a.path for a in atts if a.kind == "file"]
        if paths:
            listing = "\n".join(f"- {p}" for p in paths)
            text = (text + "\n\n" if text else "") + f"Attached files (open them with your tools):\n{listing}"
        if text:
            blocks.append({"type": "text", "text": text})
        return blocks

    def user_bubble(self, text, atts):
        bubble = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, halign=Gtk.Align.END)
        bubble.add_css_class("user-bubble")
        if atts:
            row = Gtk.Box(spacing=6, halign=Gtk.Align.END)
            for a in atts:
                row.append(attachment_widget(a, 96))
            bubble.append(row)
        if text:
            bubble.append(MarkdownView(text))
        return bubble

    def interrupt(self):
        if self.proc:
            self.proc.control({"subtype": "interrupt"})
            self.note("interrupt requested")

    def on_model_changed(self, value):
        was, self.model = self.model, value
        self.sync_effort_choices()
        self.sync_local()
        if self.remote is not None and model_program(value) != model_program(was):
            label = self.model_dd.label()
            self.model = was  # (this one goes on with its own: the one picked is the new session's)
            self.model_dd.select(was)
            self.sync_effort_choices()
            self.sync_local()
            self.remote_new_conversation(value, label)  # (a network session: a new one, on its server)
            return
        if self.remote is None and model_program(value) != model_program(was):  # (Claude's to Apple's: another program)
            self.note(f"model → {self.model_dd.label()}: a new conversation")
            self.restart_proc(resume=False)
            return
        if self.remote is None and model_program(value) == "pi" and value != was:  # (pi-claude serves one model: pi's
            # session goes on; a network session's server starts it again on the new one itself)
            self.note(f"model → {self.model_dd.label()}")
            self.restart_proc(resume=True)
            return
        if self.proc:
            self.proc.control({"subtype": "set_model", "model": value})
            self.note(f"model → {self.model_dd.label()}")
            self.refresh_effort()  # the CLI may change or drop the effort with the model
            self.refresh_context()  # different models have different windows

    def on_effort_changed(self, value):
        if model_program(self.model) == "pi":
            self.local_effort = value
        else:
            self.effort = value
        if self.proc:
            self.proc.control({"subtype": "apply_flag_settings", "settings": {"effortLevel": value}})
            self.note(f"thinking effort → {self.effort_dd.label()}")

    def sync_effort_choices(self):
        """Offer the current model's effort levels; grey the picker out if it has none."""
        info = self.models_info.get(self.model_dd.value() or "", {})
        levels = info.get("supportedEffortLevels") if info.get("supportsEffort") else None
        if levels:
            self.effort_dd.set_choices([(l, EFFORT_LABELS.get(l, l.title())) for l in levels])
            want = self.local_effort if model_program(self.model) == "pi" else self.effort
            if want in levels:
                self.effort_dd.select(want)
            self.effort_dd.set_tooltip_text("Thinking effort")
        else:
            self.effort_dd.set_choices([("", "Effort")])
            self.effort_dd.select("")
            self.effort_dd.set_tooltip_text(f"{self.model_dd.label() or 'This model'} has no effort setting")
        self.effort_dd.set_sensitive(bool(levels))

    def sync_effort(self, effort):
        if effort and effort in self.effort_dd.values:
            if model_program(self.model) == "pi":
                self.local_effort = effort
            else:
                self.effort = effort
            self.effort_dd.select(effort)

    def on_mode_changed(self, value):
        self.mode = value
        if self.proc:
            self.request({"subtype": "set_permission_mode", "mode": value},
                         lambda _r: self.mode_taken(value), lambda error: self.mode_refused(value, error))

    def mode_taken(self, value):
        self.proc.mode = value  # (what it's in: where a mode it refuses next goes back to)
        self.note(f"permission mode → {dict(MODES).get(value, value)}")

    def mode_refused(self, value, error):
        """claude said no to a mode picked (Bypass permissions, when it wasn't started able to bypass them): the
        picker and self.mode (what the chat's claude starts in next) back on the mode it's still in, unless another's
        been picked since, and why said."""
        was = getattr(self.proc, "mode", None)
        if self.mode == value and was:
            self.sync_mode(was)
        why = error.split(" because ", 1)[-1].rstrip(".") if error else "claude said no"
        still = f" (still {dict(MODES).get(was, was)})" if was else ""
        self.note(f"couldn't switch to {dict(MODES).get(value, value)}: {why}{still}")

    # ---- each chat's own model: picked in one, it isn't the others'. Kept by session id as a turn ends (chat_models);
    # a chat opened is put back on it, or on its transcript's last reply's model (one Baude hasn't kept)
    CHAT_MODELS = 500

    def keep_chat_model(self):
        if not (self.session_id and self.model) or self.resume_pending or self.model == "default":
            return
        kept = load_settings().get("chat_models") or {}
        if kept.get(self.session_id) != self.model:
            chats = {k: v for k, v in kept.items() if k != self.session_id} | {self.session_id: self.model}
            save_settings(chat_models=dict(list(chats.items())[-self.CHAT_MODELS:]))
        if model_program(self.model) == "claude":
            self.claude_model = self.model  # (where a chat goes that has no model of its own: apply_chat_model)

    def apply_chat_model(self, info):
        """Put the picker on the model the chat info names was on. A Claude transcript with no model Baude knows goes
        on Claude's last used (or claude's own default): never Apple's or one on this Mac, which can't carry it on. A
        network session's is the one its server says it's on."""
        if info.get("remote") is not None and info.get("model"):
            self.model = info["model"]
            self.model_dd.select(self.model)  # (named as its server lists it once it's said: sync_models)
            self.sync_effort_choices()
            self.sync_local()
            return
        kept = (load_settings().get("chat_models") or {}).get(info["id"])
        want = (kept if kept != "default" else None) or transcript_model(info["path"])
        models = {v: m for v, m in getattr(self, "models_info", {}).items() if v != "default"}  # (an alias, not a model)
        model = next((v for v, m in models.items() if want in (v, m.get("resolvedModel"))), None) if want else None
        if model is None and want and model_program(want) == "claude":  # ("claude-opus-5-5" for the picker's "opus")
            model = next((v for v in models if model_program(v) == "claude" and v in want), None)
        if model is None and model_program(self.model) != "claude":
            model = getattr(self, "claude_model", None)
        elif model is None:
            return
        self.model = model
        if model:
            self.model_dd.select(model)
        self.sync_effort_choices()
        self.sync_local()

    def sync_local(self):
        """Apple's model and those on this Mac: Manual their only mode, and no effort but the thinking levels of one
        that thinks; the pickers for them gone while one of them is the one."""
        local = model_program(self.model) != "claude"
        self.effort_dd.set_visible(not local or self.effort_dd.get_sensitive())  # (a local model that thinks: its levels)
        self.mode_dd.set_visible(not local)
        if local and self.mode != "default":
            self.mode = "default"
            if "default" in self.mode_dd.values:
                self.mode_dd.select("default")

    def sync_models(self, models):
        """Fill the model picker from initialize's list, labelled with real names ("Opus 5.5"), and Apple's on-device
        model after them (APPLE_MODEL), then those on this Mac (local_models). apple-claude's and pi-claude's lists are
        their one model: Claude's, as claude last listed them, stay.

        The list's "default" entry is an alias, not a model: it is dropped, and when no model
        was chosen the entry it resolves to is selected instead.
        """
        models = [m for m in models or [] if m.get("value")]
        if self.remote is not None:  # (a network session: what its server can run, not this machine)
            if not any(model_program(m["value"]) == "claude" for m in models):
                models = [m for m in getattr(self, "claude_models", []) if model_program(m["value"]) == "claude"] + models
        elif models and all(model_program(m["value"]) != "claude" for m in models):
            models = getattr(self, "claude_models", [])
        else:
            self.claude_models = models
            meta.listed(models, seed=True)  # (what this machine's claude runs: Meta's Intelligence, till Meta's own lists)
        if self.remote is None:
            models = [m for m in models if model_program(m["value"]) == "claude"] + [APPLE_MODEL] + local_models()
        self.models_info = {m["value"]: m for m in models}
        default = next((m for m in models if m["value"] == "default"), {})
        models = [m for m in models if m["value"] != "default"]
        self.model_dd.set_choices([(m["value"], m.get("displayName") or m["value"]) for m in models]
                                  or MODELS)
        want = self.model or default.get("resolvedModel")
        if want:
            hit = next((m["value"] for m in models if want in (m["value"], m.get("resolvedModel"))), want)
            self.model_dd.select(hit)
        self.sync_effort_choices()
        self.sync_local()

    def read_only_mode(self):
        """Every chat opened (a new one, or one picked in the sidebar) starts in Plan, read-only, whatever mode the
        chat before was left in."""
        self.mode = "plan"
        if self.mode_dd.values:
            self.mode_dd.select("plan")

    def sync_mode(self, mode):
        if not self.mode_dd.values:
            self.mode_dd.set_choices(MODES)
        if mode:
            self.mode = mode
            self.mode_dd.select(mode)  # (quietly: not asked of claude again)
            if self.proc is not None:
                self.proc.mode = mode  # (what claude says it's in)

    def on_launcher_toggled(self, action, value):
        try:
            if value.get_boolean():
                self.note(f"added to {LAUNCHER.where}: {LAUNCHER.install()}")
            else:
                removed = LAUNCHER.uninstall()
                self.note(f"removed from {LAUNCHER.where}" if removed else "launcher was not installed")
        except (OSError, GLib.Error) as e:
            self.note(f"launcher: {e}")
        action.set_state(GLib.Variant.new_boolean(LAUNCHER.installed()))

    @staticmethod
    def norm_dir(path):
        """The one spelling of a folder used everywhere: absolute, ~ expanded, symlinks resolved
        (claude reports its cwd resolved, so this is what it actually runs in)."""
        return os.path.realpath(os.path.expanduser(path))

    def set_cwd(self, path):
        """Make `path` the working folder: the next claude process runs in it (self.run_cwd), and so do relative
        paths. self.cwd is the folder it belongs to (main_folder: a worktree's is its repository's main checkout),
        which the status line, the sidebar's group, new sessions and Signal's glossary follow. Nothing else may
        assign either."""
        ran, self.run_cwd = self.run_cwd, self.norm_dir(path)
        self.cwd, tree = main_folder(self.run_cwd)
        MarkdownView.base_dir = self.run_cwd
        checkout = tree if tree and os.path.isdir(tree) else None  # (a worktree's chat works in it)
        moved = getattr(self, "signal", None) and \
            self.signal.place.key != signal_place(self.ref_checkout or checkout or self.cwd).key
        if moved or self.run_cwd != ran and checkout != self.ref_checkout:
            self.ref_checkout = checkout  # (another folder or checkout: its chat's checkout is looked at again)
            if getattr(self, "signal", None) and not getattr(self, "switching_chat", False):
                self.set_signal(save=False)  # (a chat being opened applies its own switches once it's in)
        if hasattr(self, "panel"):
            self.panel.git_page.chat_changed()
        if hasattr(self, "status"):
            self.update_status()
        if hasattr(self, "sidebar") and not getattr(self, "switching_chat", False):  # (a chat switched to: once it's in)
            self.sidebar.fill()  # this folder's group moves to the top

    def cwd_dialog(self):
        dlg = Gtk.FileDialog(title="Working directory", modal=True)
        dlg.set_initial_folder(Gio.File.new_for_path(self.cwd))  # open where we are
        return dlg

    def pick_folder(self):
        """Open folder…: a new session in the folder you pick."""
        dlg = self.cwd_dialog()
        def done(d, res):
            try:
                f = d.select_folder_finish(res)
            except GLib.Error:
                return
            self.switch_to(lambda: self.start_in_folder(f.get_path()))
        dlg.select_folder(self, None, done)

    def start_in_folder(self, path):
        if not os.path.isdir(path):
            self.note(f"not a folder: {path}")
            return
        self.new_session(path)

    def change_cwd(self, path):
        path = self.norm_dir(os.path.join(self.run_cwd, path))  # relative to the folder claude runs in
        if not os.path.isdir(path):
            self.note(f"not a directory: {path}")
            return
        if self.busy:
            self.note("wait for the current turn to finish (or Stop it) before changing folder")
            return
        if path == self.run_cwd:
            return
        if self.remote is not None and not self.remote.mine:  # (another machine's folders: not this one's to pick)
            self.note(f"/cd is this machine's: a session on {remote_name(self.remote)} stays in its folder there")
            return
        if self.remote is not None:  # (this machine's host process runs it: there, from its next message on)
            reply = self.remote.call({"op": "set_cwd", "session": self.session_id, "cwd": path}, timeout=10)
            if reply.get("op") != "cwd_set":
                self.note(f"folder not changed: {reply.get('error')}")
                return
            if self.session_id in self.remote.sessions:  # (as it's listed, before it next says so)
                self.remote.sessions[self.session_id] = {**self.remote.sessions[self.session_id], "cwd": reply["cwd"]}
        self.set_cwd(path)
        keep = self.session_id is not None
        self.note(f"folder → {self.short_cwd()}" + (" (conversation continues)" if keep else ""))
        self.restart_proc(resume=True)

    # ---- protocol
    def on_msg(self, proc, msg):
        if proc is not self.proc:
            return False  # leftover output from a process we already replaced
        t = msg.get("type")
        if t in ("stream_event", "assistant") and not msg.get("parent_tool_use_id") and not self.busy:
            self.set_busy(True)  # the CLI started a turn by itself
        handler = getattr(self, "h_" + str(t).lstrip("_"), None)
        if handler:
            handler(msg)
        return False

    def h_stderr(self, msg):
        own = model_program(self.model) != "claude"  # (apple-claude and pi-claude say things there for the chat)
        self.note(("" if own else "stderr: ") + msg["text"])

    def h_system(self, msg):
        st = msg.get("subtype")
        if st == "init":
            TOOL_NAMES.update(t for t in msg.get("tools") or [] if isinstance(t, str))  # (what Signal can name)
        if st == "init" and self.resume_pending:
            return  # the fresh helper process's own id: the session we show is the viewed one
        if st == "init" and self.remote is not None:  # (a network session is its server's id, whatever claude's is)
            self.update_status(f"{msg.get('model', '')}" + ("" if self.remote.mine else f"  ·  on {remote_name(self.remote)}"))
            return
        if st == "init":
            new_id = msg.get("session_id") != self.session_id
            self.session_id = msg.get("session_id")
            if new_id:
                if self.session_id and (self.signal_pending is not None or self.session_id not in
                                        (load_settings().get("signal_chats") or {})):
                    # (its switches as they are now, so the defaults changing later doesn't change them)
                    self.keep_chat_signal(self.session_id, *(self.signal_pending or (self.signal_run.get_active(),
                                                                                     self.signal_show.get_active())))
                    self.signal_pending = None
                self.sidebar.fill()
            if msg.get("cwd") and self.norm_dir(msg["cwd"]) != self.run_cwd:
                self.note(f"claude is running in {msg['cwd']}")
                self.set_cwd(msg["cwd"])  # what claude really uses wins
            self.update_status(f"{msg.get('model', '')}  ·  {len(msg.get('tools', []))} tools")
        elif st == "compact_boundary":
            self.note("— conversation compacted —")
            self.refresh_context()
        elif st == "status":
            self.sync_mode(msg.get("permissionMode"))  # e.g. leaving plan mode
            if msg.get("status") == "requesting" and not self.busy:
                self.set_busy(True)  # a turn the CLI started itself (e.g. a background task finished)
        elif st == "session_title_changed":
            self.show_title(msg.get("title"))
            self.sidebar.refresh()  # the list shows the new title
        elif st in ("task_started", "task_progress", "task_updated", "task_notification"):
            self.on_task_event(st, msg)
        elif st == "background_tasks_changed":
            self.on_tasks_changed(msg.get("tasks") or [])
        elif st == "commands_changed":
            self.completer.set_commands(msg.get("commands") or [])
        elif st not in ("hook_started", "hook_response", "thinking_tokens", "task_summary",
                        "post_turn_summary", "rate_limit_event"):
            self.note(f"[system:{st}]")

    # ---- session title (click it in the header to rename)
    def show_title(self, title):
        self.session_title = title
        self.title_label.set_text(title or "New session")
        self.set_title(f"Baude — {title}" if title else "Baude")
        # (not the sidebar: a chat being switched to fills it once it's in; a rename re-reads it)

    # ---- background tasks (commands and subagents the CLI runs after a turn ends)
    def on_task_event(self, kind, msg):
        tid = msg.get("task_id")
        if not tid:
            return
        task = self.tasks.setdefault(tid, {"task_id": tid, "status": "running"})
        if kind == "task_updated":
            task.update(msg.get("patch") or {})
        elif kind == "task_progress":  # its "description" is the current activity, not the title
            fields = {k: v for k, v in msg.items() if k not in ("type", "subtype", "uuid", "session_id")}
            fields["activity"] = fields.pop("description", None)
            task.update(fields)
        else:
            task.update({k: v for k, v in msg.items() if k not in ("type", "subtype", "uuid", "session_id")})
        if kind == "task_notification":
            task["status"] = msg.get("status") or "completed"
        card = self.tool_cards.get(task.get("tool_use_id"))
        if card is not None:
            if kind == "task_started":
                card.bind_task(task)
                if task.get("task_type") == "local_agent":
                    self.panel.link_card(card, task)
            elif kind == "task_notification" and (card.state in ("running", "background", "interrupted")
                                                  or card.task is None):
                # not on task_updated: it comes first, without the summary and output file. One settled as
                # interrupted (a chat opened as finished, settle_tasks) or never told of its task ended after all.
                card.finish_task(task)
        if kind == "task_notification" and not task.get("noted"):
            task["noted"] = True
            if (line := self.task_note(task)) is not None:  # (none while its card's in the chat: it says so)
                self.add(line)
        self.panel.update_task(task)
        self.update_tasks_indicator()

    def on_tasks_changed(self, running):
        """The CLI's current list of running tasks (endings arrive as task_notification)."""
        for t in running:
            if isinstance(t, dict) and t.get("task_id"):
                self.tasks.setdefault(t["task_id"], {"status": "running"}).update(t)
                self.panel.update_task(self.tasks[t["task_id"]])
        self.update_tasks_indicator()

    def running_tasks(self):
        return [t for t in self.tasks.values() if t.get("status") == "running"]

    def update_tasks_indicator(self):
        self.update_session_state()
        running = self.running_tasks()
        agents = sum(t.get("task_type") == "local_agent" for t in running)
        procs = len(running) - agents
        parts = ([f"{procs} background process{'es' if procs != 1 else ''}"] if procs else []) + \
                ([f"{agents} subagent{'s' if agents != 1 else ''}"] if agents else [])
        self.tasks_btn.set_visible(bool(running))
        self.tasks_btn.set_label("⚙ " + " · ".join(parts) + " running" if running else "")
        self.tasks_btn.set_tooltip_text("\n".join(t.get("description") or t["task_id"] for t in running)
                                        + "\nClick to open the side panel")

    def stop_task(self, task_id):
        self.request({"subtype": "stop_task", "task_id": task_id}, lambda _r: None)
        self.note(f"stop requested for {self.tasks.get(task_id, {}).get('description') or task_id}")

    def place_split(self):
        """Give the panel the width it was dragged to when it opens, else about a third of the window."""
        width = self.split.get_width() or self.get_width()
        if width:
            want = self.panel_width or max(320, min(460, int(width * 0.36)))
            chat = self.split.get_start_child().measure(Gtk.Orientation.HORIZONTAL, -1)[0]  # (what it can't do without)
            self.split.set_position(width - max(320, min(want, width - chat)))

    def panel_closed_by_drag(self, width):
        """Dragged on past its least and closed: what's kept is the width it was, not that."""
        self.panel_width = width
        save_settings(panel_width=width)

    def split_dragged(self):
        """The rule between the chat and the panel moved while it's held: the panel's width is yours from now on
        (saved once the dragging stops)."""
        if not self.panel_slide.following() or not (self.panel.get_visible() and self.split.get_width()):
            return
        self.panel_width = self.split.get_width() - self.split.get_position()
        if self.split_save is not None:
            GLib.source_remove(self.split_save)
        self.split_save = GLib.timeout_add(400, lambda: setattr(self, "split_save", None) or
                                           save_settings(panel_width=self.panel_width) or False)

    def show_panel(self, show=True, page=None):
        self.panel_slide.set_shown(show)
        if show:
            self.panel.show_page(page or ("agents" if any(t.get("task_type") == "local_agent"
                                                          for t in self.running_tasks()) else "processes"))

    def show_meta(self):
        """Ctrl+M (widgets.META_KEY), from anywhere in the window: Meta's page of the side panel, the panel shown if it
        was hidden, and the focus in Meta's box."""
        cover = self.capture_cover()  # (the large image, the Kubernetes or the database view: over Meta too)
        if cover is not None:
            cover.close()
        if self.completer.visible():
            self.completer.hide()  # (/'s or @'s list, not left over the chat's box)
        self.refs.forget()
        self.show_panel(True, "meta")
        self.panel.meta.composer.grab_focus()

    def h_stream_event(self, msg):
        ev, parent = msg.get("event", {}), msg.get("parent_tool_use_id")
        et = ev.get("type")
        key = lambda i: (parent, i)  # noqa: E731
        if et == "message_start":
            self.streamed_ids.add(ev["message"]["id"])
        elif et == "content_block_start":
            cb = ev["content_block"]
            if cb["type"] == "text":
                self.blocks[key(ev["index"])] = self.add(self.reply_view(), parent)
            elif cb["type"] == "thinking":  # shown once it has words (most blocks never do)
                view = ThinkingView()
                view.parent_tool = parent
                self.reply_views.append(view.md)  # (its names link as a reply's do)
                self.blocks[key(ev["index"])] = view
            elif cb["type"] in ("tool_use", "server_tool_use"):
                card = ToolCard(cb["name"], cb["id"])
                self.tool_cards[cb["id"]] = card
                self.blocks[key(ev["index"])] = self.add(card, parent)
        elif et == "content_block_delta":
            w, d = self.blocks.get(key(ev["index"])), ev["delta"]
            if w is None:
                return
            if d["type"] == "text_delta":
                w.append(d["text"])
            elif d["type"] == "thinking_delta":
                w.append(d["thinking"])
                if w.get_parent() is None and w.has_text():
                    self.add(w, w.parent_tool)
            elif d["type"] == "input_json_delta":
                w.add_partial_json(d["partial_json"])

    def h_assistant(self, msg):
        m, parent = msg["message"], msg.get("parent_tool_use_id")
        for w in self.blocks.values():  # its streamed text is complete: no frame's delay
            if isinstance(w, (MarkdownView, ThinkingView)):
                w.finish()
        if msg.get("is_api_error_message") or m.get("model") == "<synthetic>":
            for c in m.get("content", []):
                if c.get("type") == "text":
                    self.add(api_error_label(c["text"]), parent)
            return
        self.render_assistant(m.get("content", []), parent, streamed=m.get("id") in self.streamed_ids)
        if parent is None:
            self.refresh_context_soon()

    def render_assistant(self, content, parent=None, streamed=False, written_at=None):
        """Assistant content blocks. Streamed messages already drew their text and thinking."""
        for c in content if isinstance(content, list) else [{"type": "text", "text": str(content)}]:
            if c.get("type") in ("tool_use", "server_tool_use"):
                card = self.tool_cards.get(c["id"])
                if card is None:
                    card = ToolCard(c["name"], c["id"])
                    self.tool_cards[c["id"]] = card
                    self.add(card, parent)
                card.set_input(c.get("input", {}))
                if c["id"] in self.orphan_results:  # its result was drawn before it (older history)
                    self.render_tool_results([self.orphan_results.pop(c["id"])])
            elif c.get("type") == "text" and c.get("text", "").strip():
                self.queue_refs(c["text"], written_at)  # first: new names are drawn pending, not redrawn
                if not streamed:
                    self.add(self.reply_view(c["text"]), parent)
                self.add_artifact_chips(c["text"], parent)
            elif c.get("type") == "thinking" and c.get("thinking", "").strip():
                self.queue_refs(c["thinking"], written_at)  # (its names link as a reply's do)
                if not streamed:
                    view = ThinkingView(c["thinking"])
                    self.reply_views.append(view.md)
                    self.add(view, parent)

    def render_tool_results(self, content, from_file=False):
        for c in content:
            if isinstance(c, dict) and c.get("type") == "tool_result" and c.get("tool_use_id") not in self.tool_cards:
                self.orphan_results[c.get("tool_use_id")] = c
            if isinstance(c, dict) and c.get("type") == "tool_result" and c.get("tool_use_id") in self.tool_cards:
                card = self.tool_cards[c["tool_use_id"]]
                if from_file:
                    self.task_from_result(card, c.get("content"))  # before: the card waits on it
                card.set_result(c.get("content"), c.get("is_error", False))
                if card.task is None and self.task_lines:  # (its task's ending drawn before it: a later part)
                    self.card_task(card)
                if card.artifact:
                    self.register_artifact(card.artifact)

    # ---- artifacts
    def register_artifact(self, info):
        """Remember an artifact (newer info wins, but a known title is never lost)."""
        old = self.artifacts.get(info["url"], {})
        merged = {**old, **{k: v for k, v in info.items() if v}}
        if old.get("title") and merged.get("title") == "Claude artifact":
            merged["title"] = old["title"]
        self.artifacts[info["url"]] = merged
        if merged.get("title") and merged["title"] != "Claude artifact":
            ARTIFACT_TITLES[merged["url"]] = merged["title"]  # links in replies read as the title
        self.panel.update_artifact(merged)
        return merged

    def add_artifact_chips(self, text, parent=None):
        """Artifact links in a reply are drawn inline (see link_markup); here they're only
        recorded, so the side panel lists them."""
        for url in dict.fromkeys(ARTIFACT_URL.findall(text or "")):
            if url not in self.artifacts:
                self.register_artifact({"url": url, "title": "Claude artifact"})

    # ---- Signal
    def signal_menu(self):
        """The link button's dropdown: this chat's two Signal switches (shown links, and running), the defaults new
        chats start with, and Signal's page."""
        pop = Gtk.Popover()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=8, margin_bottom=6, margin_start=10,
                      margin_end=10, width_request=310)

        def row(title, detail):
            line = Gtk.Box(spacing=12)
            text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
            text.append(Gtk.Label(label=title, xalign=0))
            sub = Gtk.Label(label=detail, xalign=0, wrap=True, max_width_chars=38)
            sub.add_css_class("dim-label")
            sub.add_css_class("caption")
            text.append(sub)
            sw = Gtk.Switch(valign=Gtk.Align.CENTER)
            line.append(text)
            line.append(sw)
            box.append(line)
            return sw
        self.signal_show = row("Show links", "Draw the names Signal knows as links: files, code, commits, routes, "
                                             "Kubernetes objects, tools")
        self.signal_run = row("Activate Signal", "Look up the names in this chat's replies as they come, and let the "
                                                 "reader (a model session) read the chat")
        line = Gtk.Box(spacing=8)
        self.signal_default = Gtk.Label(xalign=0, wrap=True, hexpand=True, max_width_chars=30)
        self.signal_default.add_css_class("dim-label")
        self.signal_default.add_css_class("caption")
        line.append(self.signal_default)
        use = Gtk.Button(label="Use for new chats", valign=Gtk.Align.CENTER,
                         tooltip_text="New chats start with these two switches as they are now")
        use.add_css_class("flat")
        use.connect("clicked", lambda *_: (save_settings(signal=self.signal_run.get_active(),
                                                         signal_links=self.signal_show.get_active()),
                                           self.show_signal_default()))
        line.append(use)
        box.append(line)
        page = Gtk.Button(label="Signal Page", halign=Gtk.Align.END)
        page.add_css_class("flat")
        page.connect("clicked", lambda *_: (pop.popdown(), self.show_panel(True, "refs")))
        box.append(page)
        pop.set_child(box)
        pop.connect("show", lambda *_: self.show_signal_default())
        return pop

    def show_signal_default(self):
        on, links = self.chat_signal(None)
        self.signal_default.set_label("For this chat. New chats: " + ("links shown" if links else "links hidden") +
                                      (", activated" if on else ", not activated"))

    # ---- Signal per chat, as Remote Control is: each chat keeps its own two switches, and a new chat starts with the
    # defaults ("signal" and "signal_links" in settings; the dropdown's Use for new chats sets them)
    SIGNAL_CHATS = 500  # chats whose switches are kept (the most recently changed)

    def chat_signal(self, sid):
        """A chat's Signal switches, (on, links): its own if it has some, else the defaults for new chats."""
        s = load_settings()
        kept = (s.get("signal_chats") or {}).get(sid) if sid else None
        if isinstance(kept, list) and len(kept) == 2:
            return bool(kept[0]), bool(kept[1])
        return bool(s.get("signal")), s.get("signal_links") is not False

    def keep_chat_signal(self, sid, on, links):
        chats = {k: v for k, v in (load_settings().get("signal_chats") or {}).items() if k != sid}
        chats[sid] = [bool(on), bool(links)]
        save_settings(signal_chats=dict(list(chats.items())[-self.SIGNAL_CHATS:]))

    def apply_chat_signal(self):
        """The chat shown changed: its switches (a new chat's: the defaults, or what you set before it had an id), and
        Signal as they say."""
        on, links = self.chat_signal(self.session_id) if self.session_id else \
            (self.signal_pending or self.chat_signal(None))
        for sw, value, handler in ((self.signal_run, on, self.signal_run_handler),
                                   (self.signal_show, links, self.signal_show_handler)):
            sw.handler_block(handler)
            sw.set_active(value)
            sw.handler_unblock(handler)
        self.set_signal(save=False)

    def set_signal(self, on=None, links=None, save=True):
        """This chat's two Signal switches (None: as they are now); save keeps them for the chat. on: it looks up the
        names in the chat's replies, and the reader reads the chat. links: the names it knows are drawn as links. With
        links alone it draws what this folder's glossary already knows and what the catalogue and git say, with no
        model (and makes no glossary where there's none); with on alone it fills the glossary while the replies stay plain. A folder's Signal is
        shared by the windows that show its chats: it runs while one of their chats has it on, and only that one
        steers its reader. The replies shown are relinked either way."""
        on = self.signal_run.get_active() if on is None else on
        links = self.signal_show.get_active() if links is None else links
        if save:
            if self.session_id:
                self.keep_chat_signal(self.session_id, on, links)
            else:
                self.signal_pending = (on, links)  # (kept once the chat has an id: h_system)
        old = self.signal
        was_on = old is not None and self in old.runners
        if old is not None:
            if self.on_refs_changed in old.listeners:
                old.listeners.remove(self.on_refs_changed)
                old.status_listeners.remove(self.on_ref_status)
                old.ask_listeners.remove(self.ask_lsp)
                old.question_listeners.remove(self.ask_ref)
            old.runners.discard(self)
        folder, home = (self.ref_checkout, self.cwd) if self.ref_checkout else (self.cwd, None)
        keep = bool(self.cwd) and (on or links)  # (links alone: what's known, the catalogue's and git's)
        self.signal = Signal.for_folder(folder, home=home, halted=not on) if keep else None
        if old is not None and old is not self.signal:
            if not old.listeners:
                stopped = old.stop()  # (no window shows its chat now: everything it was doing stops)
                if save and was_on:
                    self.ref_toast("Signal is off" + (": stopped " + ", ".join(stopped) if stopped else ""))
            elif not old.runners and not old.halted:
                old.links_only()  # (another window shows its links, but no chat runs it now)
        if self.signal is not None:
            if on:
                self.signal.runners.add(self)
                self.signal.start()  # (if it was stopped)
                if save and not was_on:
                    self.ref_toast("Signal is on for this chat: the names in its replies are looked up" +
                                   (" and become links" if links else ""), show=True)
            elif not self.signal.runners and not self.signal.halted:
                stopped = self.signal.links_only()
                if save and was_on:
                    self.ref_toast("Signal stopped looking things up" + (" (" + ", ".join(stopped) + ")" if stopped
                                                                         else "") + "; its links stay")
            self.signal.listeners.append(self.on_refs_changed)
            self.signal.status_listeners.append(self.on_ref_status)
            self.signal.ask_listeners.append(self.ask_lsp)
            self.signal.question_listeners.append(self.ask_ref)
            self.use_links()
            if on and self.signal.runners <= {self}:
                self.signal.steer(self.ref_transcript())  # (a new chat with no transcript: none, not the chat before)
            elif on or self.signal.halted:  # (a chat that doesn't run it never steers one another window runs)
                self.signal.follow(self.ref_transcript())
            for view in self.assistant_views():
                self.queue_refs(view.text)
            if self.history and not self.history.todo:  # the replies further up, not drawn yet (or, while the start of
                self.queue_history_refs(self.history.earlier_replies())  # a long chat's indexed, once it is)
        elif widgets.SIGNAL is old:
            widgets.SIGNAL = None
        if old is not None and was_on and (old is not self.signal or not on) and old.runners:
            old.steer(next(iter(old.runners)).ref_transcript())  # (another window's chat runs it: its reader reads that)
        for view in self.assistant_views():
            view.relink()
        self.panel.signal_page.changed()
        self.on_ref_status()

    def links_on_focus(self):
        """This window became the active one: its own links are drawn (another window's chat may show none)."""
        before = widgets.SIGNAL
        self.use_links()
        if widgets.SIGNAL is not before:
            for view in self.assistant_views():
                view.relink()

    def use_links(self):
        """Point widgets.SIGNAL (what _inline links names with) at this window's Signal if its chat shows links, else at
        none: before this window's replies are drawn, and when it becomes the active window."""
        widgets.SIGNAL = self.signal if self.signal is not None and self.signal_show.get_active() else None

    def destroy(self):
        self.drop_signal()  # (destroyed without being closed: its Signal is let go too)
        super().destroy()
        self.let_go()

    def let_go(self):
        """The window is closed: what still runs for it stops (its timers, its file watches), and its chat is freed
        (see discard). Another window keeps the app going, and a closed one lived on till the end, chat and all,
        polling its transcript every second. (Not the rest of it: list views manage their rows' widgets themselves,
        and disposing those by hand unrefs them twice.)"""
        if getattr(self, "let_go_done", False):
            return False
        self.let_go_done = True
        for owner in (self, self.panel, self.sidebar):
            for name in ("poll_source", "expiry"):
                if (source := getattr(owner, name, None)) is not None:
                    setattr(owner, name, None)
                    if GLib.MainContext.default().find_source_by_id(source):
                        GLib.source_remove(source)
        self.tail.stop()
        self.panel.meta.close_agent()  # (Meta's session: the window's own)
        for fn in (self.on_network, self.sidebar.on_network):  # (the network's news isn't this window's any more)
            if fn in network().listeners:
                network().listeners.remove(fn)
        for mon in self.sidebar.monitors.values():
            mon.cancel()
        self.sidebar.monitors.clear()
        self.sidebar.drop_midnight()
        self.kube.close()
        Gtk.StyleContext.remove_provider_for_display(self.get_display(), self.zoom_css)  # (its zoom: the display's)
        self.clear_transcript(closing=True)
        self.reset_stream_state()  # (the cards by id, too)
        self.history = None
        return False

    def drop_signal(self):
        """The window is gone: it stops listening to its Signal (which stops, if no other window shows it), and its
        links are no longer the ones drawn."""
        ref, self.signal = self.signal, None
        if ref is None:
            return
        for listeners, fn in ((ref.listeners, self.on_refs_changed), (ref.status_listeners, self.on_ref_status),
                              (ref.ask_listeners, self.ask_lsp), (ref.question_listeners, self.ask_ref)):
            if fn in listeners:
                listeners.remove(fn)
        ran = self in ref.runners
        ref.runners.discard(self)
        if not ref.listeners:
            ref.stop()
        elif not ref.runners and not ref.halted:
            ref.links_only()  # (another window shows its links; none runs it now)
        elif ran and ref.runners:
            ref.steer(next(iter(ref.runners)).ref_transcript())  # (its reader reads the chat that runs it now)
        if widgets.SIGNAL is ref:
            widgets.SIGNAL = None

    def git_chat(self):
        """The chat the Git page keeps its state for (GitPage.chat_check): its session."""
        return self.session_id

    def ref_transcript(self):
        """The shown chat's transcript, if there is one yet."""
        return self.view_path if self.resume_pending else \
            (find_session(self.session_id) or {}).get("path") if self.session_id else None

    def follow_checkout(self, transcript=None):
        """The chat works in another checkout of its folder's repository (it moved into a worktree): Signal
        searches that one, with a glossary of its own. Looked at when a session opens, as it grows, and after
        each turn; and the reader reads what's new."""
        transcript = transcript or self.ref_transcript()
        if not transcript or not self.cwd:
            return  # (no transcript to tell from: it stays where it is)
        checkout = chat_checkout(self.cwd, transcript)
        if checkout != self.ref_checkout:
            self.ref_checkout = checkout
            if self.signal is not None:
                self.set_signal(save=False)
        if self.signal is not None and (self.signal_run.get_active() or self.signal.halted):
            self.signal.follow(transcript)  # (a chat that doesn't run it never steers one another window runs)
        self.panel.git_page.chat_changed()

    def ask_ref(self, topic, detail):
        """Signal asks, once: fetch the tool schemas from Anthropic ("tools"), or start the MCP servers
        configured here to read their tools ("mcp"). Your answer is kept in settings."""
        key = {"tools": "signal_tool_schemas", "mcp": "signal_mcp_schemas"}[topic]
        if load_settings().get(key) is not None:
            return
        if getattr(self, "ref_dialog", None) is not None:  # (asked after the one that's up)
            self.ref_questions = [q for q in getattr(self, "ref_questions", []) if q[0] != topic] + [(topic, detail)]
            return
        if topic == "tools":
            dlg = Adw.AlertDialog(
                heading="Fetch Claude Code's tool schemas?",
                body="When a reply talks about a tool (the Agent tool, TodoWrite, Read), Signal can show what it "
                     "takes and does. Baude would download Anthropic's Agent SDK package for your Claude Code "
                     f"({detail or 'its version'}; about 1.3 MB from npm, keeping only its tool types) and the tools page "
                     f"of the Claude Code docs, into {pretty_path(tool_dir())}. The switch on the Signal page changes this.",
                default_response="fetch", close_response="later")
            dlg.add_response("no", "Don't Fetch")
            dlg.add_response("fetch", "Fetch")
            dlg.set_response_appearance("fetch", Adw.ResponseAppearance.SUGGESTED)
        else:
            names = ", ".join(f"{n} ({c.get('command')})" for n, c in list(detail.items())[:6])
            dlg = Adw.AlertDialog(
                heading="Start MCP servers to read their tools?",
                body=f"This folder configures MCP servers: {names}. To show their tools' schemas, Signal would "
                     "start each one briefly, ask for its tool list, and stop it.",
                default_response="fetch", close_response="later")
            dlg.add_response("no", "Don't Start")
            dlg.add_response("fetch", "Start Them")
            dlg.set_response_appearance("fetch", Adw.ResponseAppearance.SUGGESTED)

        def answered(_d, response):
            if self.ref_dialog is not dlg:  # (answered already)
                return
            self.ref_dialog = None
            if getattr(self, "ref_questions", None):
                GLib.idle_add(lambda: self.ask_ref(*self.ref_questions.pop(0)) and False)
            if response in ("no", "fetch") and self.signal is not None:
                (self.signal.set_tool_schemas if topic == "tools" else self.signal.set_mcp_schemas)(
                    response == "fetch")
                if topic == "tools":
                    self.panel.signal_page.tools.set_active(response == "fetch")
            elif response in ("no", "fetch"):
                save_settings(**{key: response == "fetch"})
        dlg.connect("response", answered)
        self.ref_dialog = dlg
        dlg.present(self)

    def ask_lsp(self):
        """Signal wants a language server and none is fetched: ask, once (your answer is kept in settings)."""
        if getattr(self, "lsp_dialog", None) is not None or load_settings().get("signal_lsp") is not None:
            return
        what = ", ".join(f"{name} {version}" for name, version in REF_LSP_PACKAGES)
        dlg = Adw.AlertDialog(
            heading="Fetch language servers for code names?",
            body="Signal can ask a language server which definition a code name means (whose field "
                 "previous.job_id is) instead of guessing from a search. Baude would fetch "
                 f"{what} with npm (about 60 MB) into {pretty_path(lsp_dir())}, and run them read-only on this "
                 "folder's packages: pyright with each package's .venv, TypeScript with each package's own. "
                 "The switch on the Signal page changes this later.",
            default_response="fetch", close_response="later")
        dlg.add_response("no", "Don't Fetch")
        dlg.add_response("fetch", "Fetch")
        dlg.set_response_appearance("fetch", Adw.ResponseAppearance.SUGGESTED)

        def answered(_d, response):
            self.lsp_dialog = None
            if response in ("no", "fetch"):
                if self.signal is not None:
                    self.signal.set_lsp(response == "fetch")
                else:
                    save_settings(signal_lsp=response == "fetch")
                self.panel.signal_page.lsp.set_active(response == "fetch")  # (the setting is saved: it only shows it)
        dlg.connect("response", answered)
        self.lsp_dialog = dlg
        dlg.present(self)

    def assistant_views(self):
        return self.reply_views

    def reply_view(self, text=""):
        view = MarkdownView(text)
        self.reply_views.append(view)
        return view

    def queue_refs(self, text, written_at=None):
        if self.signal is not None and text:
            for s in self.signal.queue(text, self.session_id, written_at or time.time(), self.touched_paths(),
                                       look=self.signal_run.get_active()):
                self.ref_surfaces.setdefault(s, True)
            self.panel.signal_page.changed()

    HISTORY_REFS_MS = 4  # how long queue_history_refs reads at a time, between frames

    def queue_history_refs(self, items):
        """Replies earlier in a long session, not drawn yet: looked up too, newest first, after the ones on
        screen (so the Signal page covers the whole session, and links are ready when you scroll up). items:
        newest first (HistoryIndex.earlier_replies, read from the transcript only once it's needed here). A few ms of
        them at a time, between frames, and none while the chat moves (queue_history_some): all at once, a long
        chat's froze the window for half a second or more, a second after it opened."""
        if self.signal is None:
            return
        self.history_refs = items = iter(items)  # (these: not those given before, if they're not all read yet)
        GLib.idle_add(self.queue_history_some, self.history, items, priority=GLib.PRIORITY_LOW)

    def queue_history_some(self, history, items):
        """HISTORY_REFS_MS of queue_history_refs' replies, then on in the next idle moment; or on once the chats have
        been still a moment (Signal.STILL_S)."""
        if items is not self.history_refs:
            return False  # (given again since)
        if history is not self.history or self.signal is None:
            self.history_refs = None  # (another chat now, or Signal off)
            return False
        if (wait := Signal.moved_at + Signal.STILL_S - time.monotonic()) > 0:
            GLib.timeout_add(int(wait * 1000) + 1, self.queue_history_some, history, items, priority=GLib.PRIORITY_LOW)
            return False
        end = time.monotonic() + self.HISTORY_REFS_MS / 1000
        for kind, content, *rest in items:
            if kind != "assistant" or (rest and rest[0]):
                continue
            for c in content if isinstance(content, list) else [{"type": "text", "text": str(content)}]:
                if isinstance(c, dict) and (text := c.get("text") or c.get("thinking") or "").strip() and \
                        c.get("type") in ("text", "thinking"):
                    self.queue_refs(text, ref_epoch(rest[1]) if len(rest) > 1 else None)
            if time.monotonic() > end:
                GLib.idle_add(self.queue_history_some, history, items, priority=GLib.PRIORITY_LOW)
                return False
        self.history_refs = None
        return False

    def on_ref_status(self):
        """What Signal is doing changed: the button is lit while it's on and pulses while it works, and its
        tooltip says what a click does; the page says what it's doing."""
        ref = self.signal
        running = ref is not None and not ref.halted and self in ref.runners  # (this chat's: another may run it)
        busy = running and ref_busy(ref)
        shown = ref is not None and widgets.SIGNAL is ref
        for css, on in (("ref-working", busy), ("ref-on", running), ("ref-links", shown and not running)):
            (self.signal_btn.add_css_class if on else self.signal_btn.remove_css_class)(css)
        if (icon := next((w for w in walk(self.signal_btn) if isinstance(w, Gtk.Image)), None)) is not None:
            (Ticker.pulse if busy else Ticker.still)(icon)
        hidden = "" if shown else "\nIts links are hidden (Show links)"
        if ref is None:
            tip = ("Signal is off. Click for its switches: Show links draws the files, code, commits, env vars, routes, "
                   "Kubernetes objects and tools Claude names as links; Activate Signal looks them up")
        elif not running:
            tip = f"Signal shows its links but isn't running: {ref.status_text()}.\nClick for its switches"
        elif busy:
            tip = f"Signal is working: {ref.status_text()}.{hidden}\nClick to stop it (Activate Signal)"
        else:
            tip = f"Signal is on: {ref.status_text()}.{hidden}\nClick for its switches"
        self.signal_btn.set_tooltip_text(tip)
        self.panel.signal_page.status_changed()

    def ref_toast(self, title, show=False):
        t = Adw.Toast(title=html.escape(title), timeout=4)
        if show:
            t.set_button_label("Show")
            t.connect("button-clicked", lambda *_: self.show_panel(True, "refs"))
        self.toasts.add_toast(t)

    def touched_paths(self):
        """Files this session's own tools read or wrote: the ones its replies most likely mean."""
        return {p for card in self.tool_cards.values() for k in ("file_path", "notebook_path", "path")
                if isinstance(p := card.input.get(k), str)}

    def on_refs_changed(self, surfaces):
        if self.signal is None:
            return
        self.use_links()  # (this window's replies are drawn with this window's links)
        for view in self.assistant_views() if widgets.SIGNAL is self.signal else ():  # (links hidden: nothing to redraw)
            if any(s in view.text for s in surfaces):
                view.relink()
        self.panel.signal_page.changed()

    def pointer_rect(self, widget):
        """A 1px rectangle at the pointer, in widget's coordinates (where a popover should point)."""
        ok, pt = self.compute_point(widget, Graphene.Point().init(*self.pointer))
        if not ok:
            return None
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(pt.x), int(pt.y), 1, 1
        return rect

    def open_ref(self, surface, anchor, message=None):
        """A Signal link was clicked: where the reader placed it in that reply; else a file opens, a commit /
        branch / PR / tool shows its card, and everything else (several places, not found, stale, still looking)
        gets a popover."""
        dec = self.signal.decision(message, surface) if self.signal else None
        if dec is not None and dec["verdict"] == "link" and dec.get("path"):
            full = os.path.join(self.signal.root, dec["path"])
            if dec.get("role") in ("table", "column"):  # a table or column: its database
                name = re.sub(r"\([^()]*\)$", "", surface).split(".")[-1]
                code = (full, dec.get("line"))
                if dec["role"] == "column":
                    return self.open_db_name(ref_column_table(full, dec.get("line")), name, dec["path"], code,
                                             anchor, surface)
                return self.open_db_name(name, None, dec["path"], code, anchor, surface)
            elif dec.get("kind") in ("service", "k8s") and (pop := self.runs_popover(surface, dec, anchor)):
                return pop
            elif os.path.exists(full):
                open_code(full, dec.get("line"), self)
            return
        if dec is not None and dec["verdict"] == "not_in_code":  # (what the reader says it is, in this reply)
            why = REF_ABSENT_WHY.get(dec.get("why"), REF_ABSENT_WHY["not_found"])
            ref = Ref(surface, "external", "resolved", data={"note": dec.get("note") or why}) \
                if dec.get("why") == "external" else \
                Ref(surface, dec.get("kind") if dec.get("kind") in REF_KINDS else "symbol", "not_found",
                    reason=why[0].lower() + why[1:] + (f": {dec['note']}" if dec.get("note") else ""))
            pop = RefPopover(self, ref)
            pop.attach(anchor, self.pointer_rect(anchor))
            pop.popup()
            return pop
        ref = self.signal.refs.get(surface) if self.signal else None
        if (ref is None or ref.status == "pending") and self.signal and ref_hashlike(surface):
            return self.open_commit_first(surface, anchor, message)
        if ref is None:
            return
        if ref.status == "pending":  # (not looked up yet: say so, and what it turns out to be)
            return self.lookup_pending(surface, anchor, message)
        if (live := ref.data.get("db")):  # (a running database's table, collection or database: its diagram)
            if live.get("table"):
                return self.open_db_name(live["table"], None, "", None, anchor, surface)
            server = next((s for s in pg_servers_cached() if s["container"] == live.get("container")), None)
            if server is not None:
                self.schema.open(found=(server, live["database"], None, None))
            return None
        if ref.status == "resolved" and ref.kind in ("commit", "branch"):  # (its place in the history: the Git page)
            self.show_git_ref(ref)
            return
        if ref.status == "resolved" and (ref.kind in ("pr", "tool", "subagent", "skill") or ref.data.get("context")):
            self.show_ref(surface)
            return
        defs = [l for l in ref.locations if l["role"] != "used"]
        picked = ref.status == "ambiguous" and ref.data.get("checked") == "haiku" and ref.data.get("pick") and \
            ref.locations  # (Haiku chose one of the matches for the sentence: that's what a click opens)
        if ref.kind == "symbol" and ref.status in ("resolved", "ambiguous") and defs and not ref_span(surface) and \
                (all(l["role"] in ("table", "column") for l in defs) or
                 picked and ref.locations[0]["role"] in ("table", "column")):  # a table or column: its database
            return self.show_schema_for(ref, anchor)
        if picked and self.open_location(ref, 0):
            return
        if ref.status == "resolved" and ref.kind in ("file", "symbol", "constant", "config", "cli", "k8s") and \
                len(ref.locations) == 1 and not (ref.kind == "k8s" and ref_run_names(ref)) \
                and self.open_location(ref, 0):  # (a workload: the popover says where it runs, too)
            return
        pop = RefPopover(self, ref)
        pop.attach(anchor, self.pointer_rect(anchor))
        pop.popup()
        return pop

    def on_pointer(self, _ctl, x, y):
        self.pointer = (x, y)
        if self.hover_soon is None:  # (once a frame at most, after the widget under it has seen the move)
            self.hover_soon = GLib.idle_add(self.check_hover)

    def pointer_left(self):
        if self.hover_soon is not None:  # (a check on its way would light what it was on again)
            GLib.source_remove(self.hover_soon)
            self.hover_soon = None
        self.hover_link(None)

    def check_hover(self):
        self.hover_soon = None
        picked = self.pick(*self.pointer, Gtk.PickFlags.DEFAULT)
        uri = None
        if isinstance(picked, Gtk.Label):
            ok, point = self.compute_point(picked, Graphene.Point().init(*self.pointer))
            uri = hover_uri(picked, point.x, point.y) if ok else None
        self.hover_link(uri)
        return False

    def hover_link(self, uri):
        """The pointer went onto a link (uri), or off one (None). A commit or branch: its row in the Git page is lit
        while it's there. A table or column: the running database with it is looked for already (db_locate), so a
        click opens it at once, or joins the lookup on its way, rather than starting it."""
        if uri == self.hovered:
            return
        self.hovered = uri
        surface = message = None
        if uri and uri.startswith(REF_SCHEME):
            name, _, message = uri[len(REF_SCHEME):].partition("#")
            surface, message = urllib.parse.unquote(name), message or None
        ref = self.signal.refs.get(surface) if self.signal and surface else None
        git = ref if ref is not None and ref.status == "resolved" and ref.kind in ("commit", "branch") else None
        self.panel.git_page.hover(git)
        target = self.db_target(surface, message) if surface else None
        if target and (target != self.db_prefetched[0] or time.monotonic() - self.db_prefetched[1] > 20):
            self.db_prefetched = (target, time.monotonic())
            db_prefetch(target)

    def db_target(self, surface, message=None):
        """(table, column, hint) a click on a Signal link would look for in the running databases (open_ref's ways to
        a table or column), or None if it isn't one."""
        dec = self.signal.decision(message, surface) if self.signal else None
        if dec is not None and dec["verdict"] == "not_in_code":
            return None  # (the click says what the reader said)
        if dec is not None and dec["verdict"] == "link" and dec.get("path"):
            if dec.get("role") not in ("table", "column"):
                return None
            name = re.sub(r"\([^()]*\)$", "", surface).split(".")[-1]
            if dec["role"] == "column":
                return ref_column_table(os.path.join(self.signal.root, dec["path"]), dec.get("line")), name, dec["path"]
            return name, None, dec["path"]
        ref = self.signal.refs.get(surface) if self.signal else None
        if ref is None or ref.status == "pending" or ref.status == "resolved" and ref.data.get("context"):
            return None
        if (live := ref.data.get("db")):
            return (live["table"], None, "") if live.get("table") else None
        defs = [l for l in ref.locations if l["role"] != "used"]
        picked = ref.status == "ambiguous" and ref.data.get("checked") == "haiku" and ref.data.get("pick") and \
            ref.locations
        if not (ref.kind == "symbol" and ref.status in ("resolved", "ambiguous") and defs and not ref_span(surface) and
                (all(l["role"] in ("table", "column") for l in defs) or
                 picked and ref.locations[0]["role"] in ("table", "column"))):
            return None
        first = next((l for l in ref.locations if l["role"] in ("table", "column")), None)
        name = re.sub(r"\([^()]*\)$", "", ref.surface).split(".")[-1]
        if first and first["role"] == "column":
            table = ref_column_table(os.path.join(self.signal.root, first["path"]), first.get("line")) or \
                (ref.surface.split(".")[-2] if ref.surface.count(".") == 1 else None)
            return table, name, first["path"]
        return name, None, first["path"] if first else ""

    def show_ref(self, surface):
        self.show_panel(True, "refs")
        self.panel.signal_page.show_ref(surface)

    def show_git(self, rev=None, sha=None, folder=None):
        """The side panel's Git page: a branch's history (rev), or a commit (sha) highlighted in the history of a branch
        that has it; folder: another repository's."""
        self.show_panel(True, "git")
        self.panel.git_page.focus(rev, sha, folder)

    def show_git_ref(self, ref):
        """A commit or branch Signal resolved, on the Git page: a range's last commit; another repository's in its own."""
        if ref.kind == "branch":
            self.show_git(rev=(ref.data.get("ref") or "").removeprefix("refs/heads/").removeprefix("refs/remotes/")
                          or None, folder=ref.data.get("repo"))
        else:
            self.show_git(sha=ref.data.get("sha"), folder=ref.data.get("repo"))

    def runs_popover(self, surface, dec, anchor):
        """Something the reader linked that can run (a service, a Kubernetes object): a popover with its places and
        where it runs in your local clusters, rather than straight to the one place. None if no cluster could run
        it by that name."""
        ref = self.signal.refs.get(surface)
        if ref is None or ref.status not in ("resolved", "ambiguous") or \
                not any(l["path"] == dec["path"] for l in ref.locations):  # (the reader's own place, else)
            ref = Ref(surface, dec["kind"], "resolved", data={"kind": dec.get("role"), "name": surface,
                                                              "note": dec.get("note") or ""},
                      locations=[{"role": dec.get("role") or "definition", "path": dec["path"], "line": dec.get("line")}])
        if not ref_run_names(ref):
            return None
        pop = RefPopover(self, ref)
        pop.attach(anchor, self.pointer_rect(anchor))
        pop.popup()
        return pop

    def open_location(self, ref, index):
        """Open a location where it is now (it may have moved with the code). False if it's lost. One on another
        branch: its file as that branch has it, on the Code page."""
        loc = ref.locations[index]
        if loc.get("rev"):
            text = self.signal.text_on(loc)
            if text is None:
                toast(self, f"{loc['rev']} hasn't {loc['path']} any more")
                return False
            return bool(self.show_code(os.path.join(self.signal.root, loc["path"]), loc.get("line"), text=text,
                                       note=f"On {loc['rev']}: not in this checkout"))
        full, line, how = self.signal.locate(loc)
        if how == "lost":
            self.signal.mark_stale(ref.surface)
            toast(self, "The code there changed and this couldn't be found again")
            return False
        if how == "moved":
            loc["line"] = line
            self.signal.reanchor(ref.surface, index, line)
        open_code(full, line, self)
        return True

    @property
    def code_page(self):
        """The code tab shown, else the one opened last (CodePage); None with none open."""
        pages = [c for c in self.tabs.children() if isinstance(c, CodePage)]
        current = self.tabs.current()
        return current if current in pages else pages[-1] if pages else None

    def find_in_code(self):
        """Ctrl+F: find in the code tab shown, if one is; False (not handled) if not."""
        page = self.tabs.current()
        if not isinstance(page, CodePage) or not page.code.get_visible():
            return False
        page.code.open_search()
        return True

    def zoom_schema(self, steps):
        """Ctrl +/−/0 while the database schema view is open: its diagram zooms in or out about its middle, or (0)
        fits in the view. False when it isn't open."""
        if not self.schema.on_top():
            return False
        self.schema.zoom(steps)
        return True

    def zoom_code(self, steps):
        """Ctrl +/−/0 with the focus on code (a Code page, the Kubernetes view's YAML), or with a code tab shown and
        the focus in no chat (the Files page's list, say): the code's size, steps up or down (0: 100% again). False
        elsewhere, where they size the chat."""
        focus = self.get_focus()
        if not in_code(focus) and (not isinstance(self.tabs.current(), CodePage) or in_chat(focus)):
            return False
        CodeView.set_zoom(CodeView.zoom + steps * ZOOM_STEP if steps else 1.0)
        return True

    def zoom_code_scroll(self, ctl, dy):
        """Ctrl+scroll with the pointer on the Code page (or the Kubernetes view's YAML) sizes the code, not the
        chat; False anywhere else."""
        if dy == 0 or not ctl.get_current_event_state() & Gdk.ModifierType.CONTROL_MASK:
            return False
        # a scroll event has no position of its own: where the pointer is (on the surface, around the window's shadow)
        ok, x, y, _mask = self.get_surface().get_device_position(self.get_display().get_default_seat().get_pointer())
        sx, sy = self.get_surface_transform()
        if not ok or not in_code(self.pick(x - sx, y - sy, Gtk.PickFlags.DEFAULT)):
            return False
        CodeView.zoom_scroll(dy, ctl.get_unit() == Gdk.ScrollUnit.SURFACE, ctl.get_current_event_time())
        return True

    def show_code(self, path, line=None, text=None, note=None):
        """Show a file (or the text of a version of it) in a tab of its own in the main area, beside the chat (one
        open already: shown, at line). False if it isn't something to read here (a folder, a binary or very big
        file)."""
        if text is None:
            if os.path.isdir(path):
                return False
            try:
                with open(path, "rb") as f:
                    data = f.read(CODE_LIMIT + 1)
            except OSError:
                return False
            if len(data) > CODE_LIMIT or b"\0" in data[:8192]:
                return False
            text = data.decode("utf-8", "replace")
        name = os.path.basename(path) + (f" ({note})" if note else "")
        page = self.tabs.open(("code", os.path.realpath(path), note or ""), lambda: CodePage(self), name,
                              icon=themed_icon("text-x-script-symbolic", "text-x-generic-symbolic"), tooltip=path,
                              quiet=True)
        page.show(path, text, line, note)
        self.tabs.remember()  # (shown at another line: a step back and forward go through)
        return True

    @staticmethod
    def tab_closed(_key, child):
        if isinstance(child, fixview.FixesPage):
            discard(child)  # (its images, and all that holds it)

    def image_source(self):
        """What the large image view goes through with ← and →: the Feedback tab's images, or the chat's."""
        page = self.tabs.current()
        return page if isinstance(page, fixview.FixesPage) else self.transcript

    def open_fixes(self, select=True):
        """☰ → Ongoing Feedback: its tab, opened or shown (fixview.FixesPage)."""
        return self.tabs.open(("fixes",), lambda: fixview.FixesPage(self, claude_exe()), "Feedback",
                              icon="mail-send-symbolic", tooltip="The feedback on Baude, and where each stands",
                              select=select)

    def reopen_tab(self, key, line):
        """What was shown, gone back or forward to (MainTabs.back, forward): the chat, the feedback, or a file's code
        at its line (opened again, if it's still there). False if it can't be."""
        if key == "chat":
            return self.tabs.show("chat")
        if key[0] == "fixes":
            return bool(self.open_fixes())
        if key[0] == "code":
            if key[2]:  # (a version's text: only while its tab's open)
                return self.tabs.show(key)
            return os.path.exists(key[1]) and self.show_code(key[1], line)
        return False

    def go_up(self):
        """Up (⌥⌘↑): from a file's code, its folder on the Files page, the file selected. False from the chat."""
        page = self.tabs.current()
        if isinstance(page, CodePage) and page.path and os.path.exists(page.path):
            return self.show_folder(os.path.dirname(page.path), page.path)
        return False

    def show_folder(self, path, select=None):
        """A folder (or a file in it: select) on the side panel's Files page, its tree opened down to it."""
        self.panel.files_page.reveal(path, select)
        self.show_panel(True, "files")
        return True

    def show_schema_for(self, ref, anchor=None):
        """A table or column name: the database diagram, with it selected."""
        defs = [l for l in ref.locations if l["role"] in ("table", "column")]
        name = re.sub(r"\([^()]*\)$", "", ref.surface).split(".")[-1]
        first = defs[0] if defs else None
        code = (os.path.join(self.signal.root, first["path"]), first.get("line")) if first else None
        if first and first["role"] == "column":  # (its table, from the code: preview is in more than one)
            table = ref_column_table(code[0], first.get("line")) or \
                (ref.surface.split(".")[-2] if ref.surface.count(".") == 1 else None)
            return self.open_db_name(table, name, first["path"], code, anchor, ref.surface)
        return self.open_db_name(name, None, first["path"] if first else "", code, anchor, ref.surface)

    def lookup_popover(self, anchor, name, kind=None):
        pop = LookupPopover(name, kind)
        pop.attach(anchor, self.pointer_rect(anchor))
        pop.popup()
        return pop

    def github_folders(self):
        """What GitHub's lists are read for (lookup.GitHub.look): the repository this chat's folder is in."""
        return [], [self.signal.root if self.signal is not None else self.ref_checkout or self.cwd]

    @staticmethod
    def _github_changed(win):
        """GitHub's lists, or where its pull requests stand (lookup.PULLS), have changed: the replies naming one drawn
        again (their pills: widgets.pr_pill), the Git page's list and pills too."""
        if win.signal is not None:
            win.signal.github_changed()
        was = widgets.SIGNAL, MarkdownView.base_dir  # (drawn with this window's links, as it would itself: then
        win.use_links()                                # the window that was being drawn's again)
        MarkdownView.base_dir = win.run_cwd
        try:
            for view in win.assistant_views():
                if "/pull/" in view.text or re.search(r"#\d", view.text):
                    view.relink()
        finally:
            widgets.SIGNAL, MarkdownView.base_dir = was
        win.panel.git_page.pulls_changed()

    def catalogue_signal(self):
        """The Signal whose names the glossary's search lists: this chat's, or, with Signal off for it, the folder's
        own switched off (made so if there's none yet): its catalogue is wanted, not its lookups. None without a
        folder."""
        if self.signal is not None or not self.cwd:
            return self.signal
        folder, home = (self.ref_checkout, self.cwd) if self.ref_checkout else (self.cwd, None)
        return Signal.for_folder(folder, home=home, halted=True)

    def open_db_name(self, table, column, hint, code, anchor=None, title=None):
        """A table or column: find the running database that has it first, and open the schema diagram there,
        with it selected. Several databases have it (and the code's path names none): pick one. None has it: say
        so, with its definition and Try again, rather than open a database it isn't in. The popover only shows
        if finding it takes a moment, or there's something to say."""
        name = title or (f"{table}.{column}" if table and column else table or column or "")
        kind = "Database column" if column else "Database table"
        state = {"pop": None, "done": False, "run": 0}

        def popover():
            if state["pop"] is None and anchor is not None and anchor.get_root() is not None:
                state["pop"] = self.lookup_popover(anchor, name, kind)
            return state["pop"]

        def show_busy():
            if not state["done"] and (pop := popover()) is not None:
                pop.state("Finding it in the running databases…", busy=True)
            return False

        def definition():
            if code:
                open_code(code[0], code[1], self)

        def open_at(hit):
            self.schema.open(table=hit[3], column=column, hint=hint, code=code, found=hit)

        def done(run, res):
            if run != state["run"]:
                return False
            state["done"] = True
            pop = state["pop"]
            if pop is not None and pop.is_closed:
                return False  # (dismissed while it looked: nothing opens)
            if res["best"]:
                if pop is not None:
                    pop.popdown()
                open_at(res["best"])
                return False
            pop = popover()
            if pop is None:  # (no reply to point from: say it in a toast)
                toast(self, res["error"] or f"{len(res['hits'])} databases have {name}")
                return False
            if res["hits"]:
                n = len({(h[0]["container"], h[1]) for h in res["hits"]})
                pop.state(f"{n} running databases have it. Which one?",
                          choices=[(f"{h[0]['container']} › {h[1]} › {h[2]}.{h[3]}", None, lambda h=h: open_at(h))
                                   for h in res["hits"][:10]],
                          actions=[("Definition", lambda: (pop.popdown(), definition()))] if code else [])
            else:
                acts = [("Try again", lambda: start(again=True))]
                if code:
                    acts.append(("Open the definition", lambda: (pop.popdown(), definition())))
                pop.state(res["error"], actions=acts)
            return False

        def start(again=False):
            state["run"] += 1
            state["done"], run = False, state["run"]
            if again:
                pg_forget()
                show_busy()
            DB_POOL.submit(lambda: GLib.idle_add(done, run, db_locate(table, column, hint)))
            if not again:
                GLib.timeout_add(250, show_busy)
        start()
        return state

    def open_commit_first(self, surface, anchor, message=None):
        """A name shaped like a commit hash that Signal hasn't looked up yet (it waits behind every other name in the
        chat): git is asked first, at once (rev-parse, a few ms). A commit it has opens in the Git page; otherwise as any
        name not looked up yet."""
        sig = self.signal
        root = sig.root

        def work():
            sha, folder = "", None
            for top in (root, *sig.chat_repos()):  # (this repository, then the others the chat worked in)
                if sha := (_git(top, "rev-parse", "--verify", "--quiet", f"{surface}^{{commit}}") or "").strip():
                    folder = None if top == root else top
                    break
            GLib.idle_add(lambda: (self.show_git(sha=sha, folder=folder) if sha else
                                   sig.refs.get(surface) is not None and
                                   self.lookup_pending(surface, anchor, message)) and False)
        GIT_POOL.submit(work)

    def lookup_pending(self, surface, anchor, message=None):
        """A name Signal hasn't looked up yet: "Looking it up…" (it may be anything: no kind is claimed), then
        Found, with Open, or Not found, with Try again."""
        sig = self.signal
        pop = self.lookup_popover(anchor, surface)

        def show(ref):
            if ref is None or ref.status == "pending":
                pop.set_title()
                pop.state("Looking it up…", busy=True)
            elif ref.status == "not_found":
                pop.set_title(ref_kind_label(ref))
                pop.state(f"Not found: {ref.reason}.", actions=[("Try again", lambda: (sig.refresh([surface]),
                                                                                       show(sig.refs.get(surface))))])
            else:
                pop.set_title(ref_kind_label(ref))
                pop.state(f"Found. {ref_status_text(ref)}",
                          actions=[("Open", lambda: (pop.popdown(), self.open_ref(surface, anchor, message)))])
                ref_status_tip(pop.status, ref)

        def changed(surfaces):
            if pop.is_closed:
                if changed in sig.listeners:
                    sig.listeners.remove(changed)
            elif surface in surfaces:
                show(sig.refs.get(surface))
        sig.listeners.append(changed)
        pop.connect("closed", lambda *_: changed in sig.listeners and sig.listeners.remove(changed))
        show(sig.refs.get(surface))
        return pop

    def open_as_it_was(self, ref, index):
        """Open the version of a location's file that was read when it was looked up (read-only)."""
        loc = ref.locations[index]
        text = self.signal.old_text(loc.get("blob"))
        if text is None:
            toast(self, "That version of the file wasn't kept")
            return
        if load_settings().get("code_viewer", True):
            when = " ".join(x for x in (loc.get("state"), (loc.get("commit_sha") or "")[:9] and
                                        f"at {(loc.get('commit_sha') or '')[:9]}", loc.get("branch") and
                                        f"on {loc['branch']}") if x)
            self.show_code(os.path.join(self.signal.root, loc["path"]), loc.get("line"), text=text,
                           note=f"As it was when looked up ({when}): not the file as it is now.")
            return
        path = os.path.join(self.signal.dir, "as-it-was", f"{loc['blob'][:12]}-{os.path.basename(loc['path'])}")
        if not os.path.exists(path):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            os.chmod(path, 0o444)
        open_in_editor(path, loc.get("line"), self)

    # ---- background tasks rebuilt from a transcript (a viewed / tailed / resumed session has no
    # task events: those only exist on the live stream of the claude that runs it)
    def task_from_result(self, card, content):
        texts = content if isinstance(content, list) else [content]
        text = "\n".join(c.get("text", "") if isinstance(c, dict) else str(c or "") for c in texts)
        info = None
        if m := re.search(r"Command running in background with ID: (\S+?)\.?\s+Output is being written to: (\S+)",
                          text):
            info = {"task_id": m.group(1), "task_type": "local_bash", "output_file": m.group(2).rstrip("."),
                    "description": card.input.get("description") or card.input.get("command")}
        elif "Async agent launched" in text and (m := re.search(r"agentId: (\w+)", text)):
            info = {"task_id": m.group(1), "task_type": "local_agent", "description": card.input.get("description"),
                    "subagent_type": card.input.get("subagent_type"), "prompt": card.input.get("prompt")}
        if info is None or info["task_id"] in self.tasks:
            return
        info.update({"tool_use_id": card.tool_id, "is_backgrounded": True})
        self.on_task_event("task_started", info)
        self.tasks[info["task_id"]]["from_file"] = True
        if info["task_type"] == "local_agent":
            self.follow_subagent(info["task_id"], card.tool_id)

    def task_from_note(self, note):
        tid = note.get("task_id")
        if not tid:
            return
        task = self.tasks.get(tid)
        if task is None:  # a task whose start isn't in what's shown (older history, a Monitor)
            card = self.tool_cards.get(note.get("tool_use_id"))
            kind = "local_agent" if card is not None and card.base in ("Agent", "Task") else "local_bash"
            self.on_task_event("task_started", {"task_id": tid, "tool_use_id": note.get("tool_use_id"),
                                                "task_type": kind, "description": note.get("summary")})
            task = self.tasks[tid]
            task["from_file"] = True
        task["noted"] = True  # (its line, if it gets one, is drawn from the transcript's own note: render_history)
        self.on_task_event("task_notification", {
            "task_id": tid, "tool_use_id": note.get("tool_use_id") or task.get("tool_use_id"),
            # the card shows what the task produced: a subagent's report, else the one-line summary
            "status": note.get("status") or "completed", "summary": note.get("result") or note.get("summary"),
            "output_file": note.get("output_file") or task.get("output_file")})

    def subagent_path(self, agent_id):
        base = getattr(self, "history_path", None)
        return os.path.join(base[:-len(".jsonl")], "subagents", f"agent-{agent_id}.jsonl") \
            if base and base.endswith(".jsonl") else None

    def follow_subagent(self, agent_id, tool_use_id):
        """Draw a subagent's own transcript in its side-panel view, and keep reading it."""
        path = self.subagent_path(agent_id)
        if not path:
            return
        self.sub_tails[agent_id] = {"path": path, "offset": 0, "partial": b"", "tool_use_id": tool_use_id,
                                    "first": True}
        self.read_subagent(agent_id)

    def read_subagent(self, agent_id):
        st = self.sub_tails.get(agent_id)
        if st is None or not os.path.exists(st["path"]):
            return
        lines, st["offset"], st["partial"] = read_appended(st["path"], st["offset"], st["partial"])
        for kind, content, *rest in history_items(lines, sidechain=True):
            if kind == "assistant":
                self.render_assistant(content, parent=st["tool_use_id"],
                                      written_at=ref_epoch(rest[1]) if len(rest) > 1 else None)
            elif kind == "tool_results":
                self.render_tool_results(content)
            elif kind == "user" and st["first"]:
                st["first"] = False  # its prompt: the panel shows that already

    def read_subagents(self):
        for agent_id in list(self.sub_tails):
            self.read_subagent(agent_id)

    def h_user(self, msg):
        content = msg.get("message", {}).get("content")
        if isinstance(content, list):  # a plain string is the echo of our own prompt
            self.render_tool_results(content)
            if not msg.get("parent_tool_use_id"):
                self.refresh_context_soon()

    # ---- history
    # items drawn when a session opens, and per earlier chunk (loaded as you scroll near the top, or by
    # "Show earlier messages"): only the last screen is in view, and each item costs layout time
    HISTORY_CHUNK = 60
    PLACES = {}  # session id -> where its chat was left (chat_place): every window's, kept over a restart
    MAX_PLACES = 500

    def chat_place(self):
        """Where the chat is scrolled to, for opening it there again (open_at): measured from its end (what's added at
        its top as you scroll up doesn't move it), and the earlier chunk at the top of the view, if it's in one
        (view_chunk). None if it was never laid out."""
        adj = self.scroller.get_vadjustment()
        if adj.get_page_size() <= 0:
            return None
        from_bottom = max(0.0, adj.get_upper() - adj.get_page_size() - adj.get_value())
        at_end = self.stick_bottom or from_bottom <= 1
        return {"from_bottom": from_bottom, "at_end": at_end, "chunk": None if at_end else self.view_chunk()}

    def view_chunk(self):
        """The earlier chunk at the top of the view, as [where it starts (its first item's line sum), how far into it
        the view's top is]; None if that's in the newest part (drawn on opening anyway)."""
        hist = getattr(self, "history_box", None)
        top, _page = self.view_top()
        slot = hist.get_first_child() if hist is not None and top is not None else None
        while slot is not None:
            ok, b = slot.compute_bounds(self.transcript)
            if ok and b.get_y() <= top < b.get_y() + b.get_height() and slot.first_sum is not None:
                return [slot.first_sum, top - b.get_y()]
            slot = slot.get_next_sibling()
        return None

    def place_chunk(self, chunk):
        """Open at an earlier chunk (view_chunk's): that one drawn now, the view put back in it once it's laid out
        (on_layout), and held there as what's around it is drawn. Only that one: what was drawn ahead of the reader isn't
        drawn again at once. False if it isn't there to draw (the transcript was rewritten)."""
        first_sum, offset = chunk
        self.history.finish()
        self.make_chunk_slots()
        slots = self.chunk_slots or []
        slot = next((s for s in slots if s.first_sum == first_sum), None)
        if slot is None:  # (the transcript grew since: its chunks begin elsewhere now; the one with that item in it)
            sums = self.history.sums
            at = next((i for i in range(len(sums)) if sums[i] == first_sum), None)
            slot = next((s for s in slots if at is not None and s.span[0] <= at < s.span[1]), None)
        if slot is None:
            return False
        self.fill_now(slot)
        self.place_at = (slot, float(offset))
        return True

    def put_place(self):
        """After the layout pass a chunk opened at (place_chunk) is in: the view's top where it was in it."""
        slot, offset = self.place_at
        ok, b = slot.compute_bounds(self.transcript)
        top, _page = self.view_top()
        if not ok or top is None or b.get_height() <= 0:
            return False
        self.place_at = None
        self.hold_value(self.scroller.get_vadjustment().get_value() + b.get_y() + offset - top)
        return True

    def remember_place(self):
        """The open chat's place remembered (chat_place), as it's left for another."""
        if self.session_id and (place := self.chat_place()) is not None:
            self.PLACES.pop(self.session_id, None)
            self.PLACES[self.session_id] = place
            while len(self.PLACES) > self.MAX_PLACES:  # (the longest unopened go)
                del self.PLACES[next(iter(self.PLACES))]

    def open_at(self, place):
        """A chat just drawn shown where it was left (place: chat_place), the earlier chunk it was in drawn back
        (place_chunk); never left before, at its end. There at once and held there while it lays out (on_layout: images
        and code come in sizes later), so it doesn't glide down to its end or move under you."""
        if place and not place.get("at_end"):
            self.stick_bottom = False
            if place.get("chunk") and self.place_chunk(place["chunk"]):
                return
            from_bottom = float(place.get("from_bottom") or 0)
        else:
            self.stick_bottom, from_bottom = True, 0.0
        self.restore_scroll = (from_bottom, time.monotonic() + 2.0)

    def load_history(self, path, live=False):
        """Draw the newest part of a saved session; older parts load on demand from the top."""
        self.history = HistoryIndex(path)
        items = self.history.load(self.HISTORY_CHUNK)
        self.history_path, self.history_final = path, not live  # (finished: no task in it is running: settle_tasks)
        self.earlier_btn = Gtk.Button(halign=Gtk.Align.CENTER, visible=False)
        self.earlier_btn.add_css_class("pill")
        self.earlier_btn.connect("clicked", lambda *_: self.show_earlier())
        self.add(self.earlier_btn)
        self.history_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, visible=False)  # (shown once one's drawn)
        self.history_box.is_history, self.chunk_slots = True, None
        self.add(self.history_box)
        self.drop_fills()
        self.make_chunk_slots()
        self.render_history(items, final=not live)
        if self.history.todo:  # (the start of a long one: indexed in idle time, then counted and read by Signal)
            GLib.idle_add(self.index_history, self.history, priority=GLib.PRIORITY_LOW)
        else:
            self.queue_history_refs(self.history.earlier_replies())
        self.update_earlier_btn()
        self.resumed_note = bool(items)  # "— resumed —" goes in when you first write, not on opening
        self.open_at(self.PLACES.get(self.session_id))
        GLib.timeout_add(self.FILL_AFTER_MS, lambda: (self.plan_fill(), False)[1])

    def update_earlier_btn(self):
        n = self.history.count() if self.history else 0
        self.earlier_btn.set_label("Show earlier messages" if n is None else f"Show earlier messages ({n} more)")
        top = self.history_box.get_first_child() if self.history else None
        # (not once the chat's start is drawn, out of turn: nothing's earlier than that)
        self.earlier_btn.set_visible(bool(self.history and self.history.more) and not getattr(top, "filled", False))

    # ---- the earlier chunks of a long chat: each has its place from the start (make_chunk_slots), as tall as its items
    # are likely to be (estimate_slots), so the chat's about its full height at once and the scrollbar's thumb doesn't
    # shrink and jump as they're drawn; each is drawn a little a frame (fill_frame): the one under the view or near it
    # first, the rest ahead of the reader while the chat's still.
    PX_PER_ITEM = 100  # (an item's height, till some are drawn: about the average of long chats)
    FILL_NEAR = 3.0  # screens: a chunk this near the view is drawn even while it's scrolled
    FILL_BUDGET, FILL_BUDGET_STILL = 0.003, 0.008  # s of making items a frame: while the chat's scrolled, still
    FILL_AFTER_MS = 600  # (after opening: what's earlier is drawn ahead from then on)

    def make_chunk_slots(self):
        """The earlier chunks' places, made once it's known how many: a chunk put in above what's drawn moved everything
        below it, and GTK styled all of that again (a quarter of a second a chunk, deep into a long chat). Each knows
        which items it's for (span: as HistoryIndex.take numbers them), and has a spacer at its top that takes up what
        its estimated height has over what's drawn in it, so what's drawn sits at its bottom, next to what's below."""
        if self.chunk_slots is not None or (n := self.history.count()) is None:
            return
        slots = []
        for i in range(-(-n // self.HISTORY_CHUNK)):
            end = n - i * self.HISTORY_CHUNK
            slot = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, vexpand=False)  # (its spacer's expanding
            slot.span, slot.filled = (max(0, end - self.HISTORY_CHUNK), end), False           # goes no further)
            slot.first_sum = self.history.sums[slot.span[0]] if slot.span[0] < end else None
            slot.spacer = Gtk.Box(vexpand=True)
            slot.append(slot.spacer)
            slot.set_margin_bottom(10 if i else 0)  # (the chunk below it; the lowest has the chat's own spacing)
            slots.append(slot)
        self.chunk_slots = slots  # (the ones not drawn yet: the lowest, next to what's drawn on opening, first)
        for slot in slots:
            self.history_box.prepend(slot)  # (above what's drawn: the newest's, or a rewritten transcript's chunks)
        self.history_box.set_visible(self.history_box.get_first_child() is not None)
        self.estimate_slots()
        self.estimate_soon()  # (again once what's drawn is laid out)

    def px_per_item(self):
        """How tall an item of this chat's is, on average: what's drawn (the newest, the chunks drawn) over how many."""
        height, items = 0.0, 0
        hist = self.history_box
        child = hist.get_first_child()
        while child is not None:
            if child.filled:
                height += child.get_height() + child.get_margin_bottom()
                items += child.span[1] - child.span[0]
            child = child.get_next_sibling()
        ok, hb = hist.compute_bounds(self.transcript)
        newest = self.history.shown - self.history.taken_count
        if ok and newest > 0 and self.transcript.get_height() > 0:
            height += self.transcript.get_height() - hb.get_y() - hb.get_height()
            items += newest
        return height / items if items > 0 and height > 0 else self.PX_PER_ITEM

    def estimate_slots(self):
        """Each undrawn chunk's place as tall as its items are likely to be (px_per_item): set again only when that's
        changed by a tenth, since each change moves the thumb (the view's held: hold_view_anchor)."""
        if not self.chunk_slots and not self.fill_jobs:
            return
        per = self.px_per_item()
        if abs(per - self.estimated_per) < per / 10:
            return
        self.estimated_per, self.above_changed = per, True
        for slot in (self.chunk_slots or []) + [job.slot for job in self.fill_jobs]:
            slot.set_size_request(-1, round(per * (slot.span[1] - slot.span[0])))

    def estimate_soon(self):
        """estimate_slots once what's drawn now has been laid out: as a frame begins (in GTK's update, before its layout,
        so the view's hold sees the change in the same frame: sized after a layout pass, it took more passes than GTK
        runs in a frame, and a frame was drawn with the view thousands of px off)."""
        if self.estimate_tick is None:
            self.estimate_tick = [0, self.scroller.add_tick_callback(self._estimate_frame)]

    def _estimate_frame(self, _widget, _clock):
        self.estimate_tick[0] += 1
        if self.estimate_tick[0] < 2:  # (the frame after next: what was added is laid out by then)
            return GLib.SOURCE_CONTINUE
        self.estimate_tick = None
        if self.zoom_job is None and self.history is not None:
            self.estimate_slots()
        return GLib.SOURCE_REMOVE

    def index_history(self, history):
        """A slice of indexing the start of a long chat (HistoryIndex.index_some), between frames."""
        if history is not self.history:
            return False  # (another chat now)
        if history.index_some():
            return True
        self.make_chunk_slots()
        self.update_earlier_btn()
        self.queue_history_refs(history.earlier_replies())
        self.plan_fill()
        return False

    def show_earlier(self):
        """Draw the previous chunk above what's shown, at once (the Show earlier button; a place put back: open_at),
        keeping the view where it was."""
        if not (self.history and self.history.more):
            return
        self.history.finish()  # (scrolled up before the start of a long chat was indexed: now)
        self.make_chunk_slots()
        slot = self.history_box.get_last_child()
        while slot is not None and slot.filled:
            slot = slot.get_prev_sibling()
        if slot is None:  # (nothing in it to draw after all)
            self.update_earlier_btn()
            return
        adj = self.scroller.get_vadjustment()
        before = adj.get_upper()
        self.fill_now(slot)
        self.stick_bottom = False

        # once the chunk is laid out, shift the view by what was added above it (only once: later growth, like a reply
        # streaming in at the bottom, must not move it), right after the layout pass it's in (on_layout), so the frame
        # is drawn with it: at notify::upper the frame was drawn first, the view a chunk's height off (6000 px of
        # screenshots), and set back from where it was, not where it's been scrolled to since
        self.cancel_keep_place()
        # if the layout settles without a size change, don't leave it armed: fired much later (by
        # a zoom, a new message) it would throw the view off by the old chunk's height
        expire = GLib.timeout_add(1500, lambda: (self.cancel_keep_place(), False)[1])
        self.keep_place = (adj, before, expire)

    def fill_now(self, slot):
        """Draw a chunk into its place at once (show_earlier, place_chunk), the part of it made already too."""
        job = next((job for job in self.fill_jobs if job.slot is slot), None) or self.start_fill(slot)
        self.fill_jobs.remove(job)
        while job.items:
            self.fill_item(job)
        while job.hidden:
            job.hidden.pop().set_visible(True)
        self.fill_done(job)

    def start_fill(self, slot):
        """Begin drawing a chunk into its place (its items read again as they're made: HistoryIndex.take), first."""
        self.chunk_slots.remove(slot)
        job = ChunkFill(slot, self.history.take(*slot.span))
        slot.sums = {line_sum for _start, line_sum in job.items}  # (what it draws: not drawn again after a rewrite)
        self.fill_jobs.appendleft(job)
        return job

    def history_rewritten(self):
        """The transcript was rewritten since it opened: what's left to draw indexed again, with places of its own above
        what's drawn (the places not drawn yet go; what's made of one part drawn stays)."""
        for slot in self.chunk_slots:
            self.history_box.remove(slot)
        jobs = list(self.fill_jobs)
        self.fill_jobs.clear()
        for job in jobs:
            while job.hidden:
                job.hidden.pop().set_visible(True)
            job.broken = False
            self.fill_done(job)
        drawn = set()
        child = self.history_box.get_first_child()
        while child is not None:
            drawn |= getattr(child, "sums", set())
            child = child.get_next_sibling()
        self.history.reindex(drawn)
        self.chunk_slots, self.estimated_per = None, 0
        self.make_chunk_slots()
        self.update_earlier_btn()

    def fill_item(self, job):
        """Make the next item of a chunk being drawn, hidden (hidden, GTK neither styles nor lays it out)."""
        item = self.history.item_at(job.items.popleft())
        if item is None:  # (not the line indexed: the rest of the chunk isn't either)
            job.broken = True
            job.items.clear()
        before = set(self.tool_cards)
        self.render_target = job.slot
        try:
            self.render_history([item] if item is not None else [], final=False)
        finally:
            self.render_target = None
        job.calls.update(tid for tid in self.tool_cards if tid not in before)
        if not job.items:  # (its calls that never got a result were interrupted; a later chunk's result puts it right)
            for tid in job.calls:
                card = self.tool_cards.get(tid)
                if card is not None and card.state == "running" and tid not in self.orphan_results:
                    card.set_state("interrupted")
            if getattr(self, "history_final", False):  # (and its background tasks that never ended, as when it opened)
                self.settle_tasks()
                self.show_cut(self.cut_state())
        for w in [w for w in job.hidden if w.get_parent() is not job.slot]:  # (a call gone into a run's group: the
            w.set_visible(True)                                               # group's hidden instead)
            job.hidden.remove(w)
        made, child = [], job.slot.get_last_child()
        while child is not None and child.get_visible() and child is not job.slot.spacer:
            child.set_visible(False)
            made.append(child)
            child = child.get_prev_sibling()
        job.hidden.extend(reversed(made))

    def fill_done(self, job):
        """A chunk's all drawn: its place as tall as it is now."""
        slot = job.slot
        slot.set_size_request(-1, -1)
        if slot.spacer is not None:
            slot.remove(slot.spacer)
            slot.spacer = None
        slot.filled, self.above_changed = True, True
        self.estimate_soon()
        self.update_earlier_btn()
        if job.broken:
            self.history_rewritten()

    def drop_fills(self):
        """Another chat: nothing of the one before is drawn on."""
        self.fill_jobs.clear()
        if self.fill_tick is not None:
            self.scroller.remove_tick_callback(self.fill_tick)
            self.fill_tick = None
        if self.estimate_tick is not None:
            self.scroller.remove_tick_callback(self.estimate_tick[1])
            self.estimate_tick = None
        self.estimated_per, self.cull_at, self.place_at = 0, None, None

    def plan_fill(self):
        """Draw on, a frame at a time, if there's a chunk to draw (fill_frame picks which)."""
        if self.fill_tick is None and self.history is not None and (self.chunk_slots or self.fill_jobs):
            self.fill_tick = self.scroller.add_tick_callback(self.fill_frame)

    def slot_away(self, slot, top, page):
        """How far a chunk's place is from the view, in px (0: in it)."""
        ok, b = slot.compute_bounds(self.transcript)
        return max(0.0, top - (b.get_y() + b.get_height()), b.get_y() - (top + page)) if ok else float("inf")

    def next_fill(self, moving):
        """The chunk to draw on now: the one nearest the view within FILL_NEAR screens (a thumb dragged into a part not
        drawn yet: what's under the view first), else, while the chat's still, the lowest not drawn (ahead of the
        reader); None for none now."""
        top, page = self.view_top()
        if top is None:
            return None
        # (a thumb dragged fast, half a screen a frame: what it passes by isn't drawn, nor read)
        self.fill_sweeping = moving and abs(top - getattr(self, "fill_top", top)) > page / 2
        self.fill_top = top
        if self.fill_sweeping:
            return None
        near = [(self.slot_away(job.slot, top, page), i, job) for i, job in enumerate(self.fill_jobs)]
        near += [(self.slot_away(slot, top, page), len(near) + i, slot) for i, slot in enumerate(self.chunk_slots or ())]
        near = [n for n in near if n[0] < self.FILL_NEAR * page]
        if near:
            what = min(near, key=lambda n: (n[0], n[1]))[2]
        elif moving or self.busy:
            return None
        elif self.fill_jobs:
            what = self.fill_jobs[0]
        elif self.chunk_slots:
            what = self.chunk_slots[0]
        else:
            return None
        if isinstance(what, ChunkFill):
            self.fill_jobs.remove(what)
            self.fill_jobs.appendleft(what)
            return what
        return self.start_fill(what)

    def fill_frame(self, _widget, clock):
        """Each frame: draw on the chunk next_fill picks, making its items for FILL_BUDGET (FILL_BUDGET_STILL while the
        chat's still), then showing them from the bottom up: one a frame while it's scrolled; while it's still, twice as
        many each frame that keeps its time, half after one that doesn't. What's shown goes in above what's read, where
        the view's hold keeps it still."""
        if self.zoom_job is not None or self.history is None:  # (a zoom brings messages back a few a frame itself)
            self.fill_tick = None
            return GLib.SOURCE_REMOVE
        moving = self.chat_moving()
        job = self.next_fill(moving)
        if job is None:
            if self.fill_sweeping:
                return GLib.SOURCE_CONTINUE  # (till it slows down over a part to draw)
            self.fill_tick = None
            return GLib.SOURCE_REMOVE
        if job.items:
            end = time.perf_counter() + (self.FILL_BUDGET if moving else self.FILL_BUDGET_STILL)
            while job.items and time.perf_counter() < end:
                self.fill_item(job)
            return GLib.SOURCE_CONTINUE  # (shown from the next frame)
        frame = clock.get_frame_time()
        late, self.fill_frame_at = frame - getattr(self, "fill_frame_at", frame) > 20_000, frame
        self.fill_reveal = 1 if moving else max(1, self.fill_reveal // 2) if late else min(30, self.fill_reveal * 2)
        for _ in range(self.fill_reveal):
            if not job.hidden:
                break
            job.hidden.pop().set_visible(True)  # (the newest first: next to what's shown)
            self.above_changed = True
        if not job.hidden:
            self.fill_jobs.remove(job)
            self.fill_done(job)
        return GLib.SOURCE_CONTINUE

    def hold_keep_place(self, held):
        """After a layout pass: the view moved by what a chunk drawn above added, once it's laid out (show_earlier),
        unless the message at the view's top has held it already (held: hold_view_anchor's)."""
        adj, before, _expire = self.keep_place
        if adj.get_upper() != before:
            self.cancel_keep_place()
            if held is None:
                self.hold_value(adj.get_value() + adj.get_upper() - before)

    def cancel_keep_place(self):
        pending, self.keep_place = getattr(self, "keep_place", None), None
        if pending:
            _adj, _before, expire = pending
            GLib.source_remove(expire) if GLib.MainContext.default().find_source_by_id(expire) else None

    def render_history(self, items, final=True):
        made = set(self.tool_cards)
        for kind, content, *rest in items:
            if kind == "tool_results":
                self.render_tool_results(content, from_file=True)
            elif kind == "artifact":
                if content["url"] not in self.artifacts:  # not already shown by its tool call
                    if not content.get("title"):
                        content["title"] = html_title(content.get("path")) or "Claude artifact"
                    info = self.register_artifact(content)
                    self.add(sel_label("Published " + link_markup(info["url"]), css="sysnote"))
            elif kind == "task_note":
                self.task_from_note(content)  # (its card, if it's drawn, says it ended)
                if (line := self.task_note(content)) is not None:
                    self.add(line)
            elif kind == "compact_summary":  # (what the CLI wrote for itself on compacting: folded)
                fold = Gtk.Expander(label="Summary of the earlier conversation", child=MarkdownView(content))
                fold.add_css_class("sysnote")
                self.add(fold)
            elif kind == "ci_event":  # (what the desktop app says of a pull request it's watching: folded)
                fold = Gtk.Expander(label=content["title"], child=MarkdownView(content["text"]))
                fold.add_css_class("sysnote")
                self.add(fold)
            elif kind == "assistant" and rest and rest[0]:
                self.add(api_error_label(_first_text(content) or ""))
            elif kind == "assistant":
                self.render_assistant(content, written_at=ref_epoch(rest[1]) if len(rest) > 1 else None)
            else:
                text = "\n\n".join(clean_prompt(t) for t in _prompt_texts(content))
                atts = [Attachment("image", "image", c["source"].get("media_type"),
                                   base64.b64decode(c["source"]["data"]))
                        for c in (content if isinstance(content, list) else [])
                        if isinstance(c, dict) and c.get("type") == "image"
                        and c.get("source", {}).get("type") == "base64"]
                atts += [Attachment(c.get("title") or "document.pdf", "file", "application/pdf")
                         for c in (content if isinstance(content, list) else [])
                         if isinstance(c, dict) and c.get("type") == "document"]
                self.add(self.user_bubble(text, atts))
        # calls that never got a result were interrupted; don't spin forever
        for tid, card in self.tool_cards.items():
            if final and tid not in made and card.state == "running" and tid not in self.orphan_results:
                card.set_state("interrupted")
        if final:
            self.settle_tasks()
            self.show_cut(self.cut_state())

    def settle_tasks(self):
        """Background tasks (commands, subagents) a finished session's transcript never heard the end of: interrupted,
        not running for ever. When it opens, and as each earlier part is drawn (fill_item): one started there and
        never ended came back as running, its spinner going, counted in the header, its subagent followed again."""
        for task in self.tasks.values():
            if task.get("from_file") and task.get("status") == "running":
                task["status"] = "interrupted"
                card = self.tool_cards.get(task.get("tool_use_id"))
                if card is not None and card.state in ("running", "background"):
                    card.set_state("interrupted")
                self.panel.update_task(task)
        self.update_tasks_indicator()

    # ---- sessions on the network (baudelib.server): opened from their transcripts' mirrors here, steered through
    # their servers (start_remote_proc), started from the sidebar while a group on the network is shown
    def open_remote_soon(self, info):
        """Open a network session once its transcript's here, what's new in it fetched from its server (sync_remote);
        as it was last seen if its server's gone."""
        r = info["remote"]
        self.opening_remote = info["id"]
        if r.state != "alive":
            if r.mine:  # (this machine's host process, gone after a long idle: started again, and taken up then)
                self.local_server(lambda _r: None)
            self.switch_to(lambda: self.resume_session(info))
            return

        def fetched():
            if self.opening_remote == info["id"] and not getattr(self, "let_go_done", False):
                self.switch_to(lambda: self.resume_session(info))
        self.sync_remote(r, info["id"], fetched)

    def open_remote(self, info):
        """A session on a server on the network: shown from its transcript's mirror here (Remote.mirror) as a chat
        Baude isn't running is, followed as its server writes it (on_network: sync_remote), and talked to through its
        server (start_remote_proc) as Baude's own claude is. Its folder is this window's only when its server is on
        this machine: links to files elsewhere are fetched from it (open_remote_link)."""
        r = info["remote"]
        self.remember_own_writes()
        if self.busy and self.proc is not None:
            self.detach_proc()  # (the turn goes on: detach_proc)
        self.clear_transcript()
        self.switching_chat = True
        cwd = info.get("cwd")
        if baudelib.client.is_here(r.info) and cwd and os.path.isdir(cwd):
            self.set_cwd(cwd)
        elif cwd:
            MarkdownView.base_dir = cwd  # (its relative links are its server's: not one of this machine's files)
        self.reset_stream_state()
        self.session_id, self.remote, self.desktop_chat = info["id"], r, False
        path = r.mirror(info["id"])
        if not os.path.exists(path) and not r.local:  # (one on this machine is read where it is, once it's there)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            open(path, "a").close()
        self.apply_chat_model({**info, "path": path})
        self.signal_pending, self.switching_chat = None, False
        self.ref_checkout = None
        self.apply_chat_signal()
        self.view_session(path)
        self.show_title(info.get("title"))
        self.read_only_mode()
        if r.state == "alive" and (r.sessions.get(info["id"]) or {}).get("state") in ("working", "waiting") \
                and not self.live_elsewhere():  # (at a turn: live, as it goes on, Stop and all; attached once, so
            self.begin_resume()  # what it asks is answered through the one that's attached)
            self.set_busy(True)
        else:
            self.restart_proc(resume=True)  # (start_remote_proc: what it asks, its models; all it says once you write)
        self.apply_read_only()
        self.sidebar.fill()

    def sync_remote(self, r, sid, then=None):
        """Fetch what a network session's transcript has that its mirror here hasn't, on a thread; then() after (its
        subagents' are fetched after that, not waited for). One fetch at a time: one asked for meanwhile is made after
        it."""
        key = (r.key, sid)
        if (job := self.remote_syncing.get(key)) is not None:
            job["again"] = True
            job["then"] += [then] if then else []
            return
        job = self.remote_syncing[key] = {"again": False, "then": [then] if then else []}

        def work():
            try:
                r.sync(sid, subagents=False)
            finally:  # (whatever went wrong, the next fetch isn't held up by this one)
                on_main(done)
            if r.subagents_behind(sid):
                r.sync_subagents(sid)

        def done():
            if job["again"]:
                job["again"] = False
                threading.Thread(target=work, daemon=True).start()
                return
            self.remote_syncing.pop(key, None)
            for fn in job["then"]:
                fn()
        threading.Thread(target=work, daemon=True).start()

    def on_network(self):
        """The network changed: the open network session's transcript fetched as it grows (its subagents' too), its
        title as it's listed (renamed in another window, or elsewhere), the banner as it is now."""
        if getattr(self, "let_go_done", False):
            return
        self.check_host_version()
        if (app := self.get_application()) is not None:
            app.show_update_bars()  # (This Mac's host process waiting on its commands to restart: info's update)
        if self.remote is None:
            return
        listed = self.remote.sessions.get(self.session_id)
        if listed and listed.get("title") and listed["title"] != getattr(self, "session_title", None):
            self.show_title(listed["title"])
        if self.resume_pending and self.remote.local and self.remote.direct(self.session_id) and \
                self.view_path != self.remote.mirror(self.session_id):  # (on this machine: its transcript's there now)
            self.view_session(self.remote.mirror(self.session_id))
        if listed and self.remote.state == "alive" and \
                ((listed.get("size") or 0) != self.remote.mirrored(self.session_id) or
                 self.remote.subagents_behind(self.session_id)):
            self.sync_remote(self.remote, self.session_id)
        if self.proc is None and self.resume_pending and self.remote.state == "alive" and \
                time.monotonic() - getattr(self, "remote_tried", 0.0) > 10:
            self.start_remote_proc()  # (its server's there again, or at last: what it asks, its models)
        self.update_banner()
        self.update_session_state()
        if bool(self.cut_state()) != (getattr(self, "cut_row", None) is not None):
            self.show_cut(self.cut_state())

    def remote_view(self):
        """A network session with nothing of its server's attached (gone, or not there): watched from its mirror again,
        as it's fetched, the banner saying why there's nothing to write in."""
        if self.remote is None or self.resume_pending:
            self.update_banner()
            return
        path = self.remote.mirror(self.session_id)
        self.resume_pending, self.view_path = True, path
        try:
            self.tail.start(path, os.path.getsize(path))
        except OSError:
            self.tail.start(path, 0)
        self.update_banner()

    def host_form(self):
        """Add a host's fields and what they do, Add a host…'s (ask_add_host) and Meta's add_host form's: where ssh goes
        (an alias in ~/.ssh/config, a host, or user@host), and its host process's command there, if
        baude_headless.py isn't on its PATH. Kept from then on, reached or not."""
        dest = Gtk.Entry(placeholder_text="user@host, or an ssh alias", activates_default=True, width_chars=30)
        command = Gtk.Entry(placeholder_text=f"{baudelib.wire.SSH_COMMAND}, on its PATH", activates_default=True)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.append(dest)
        box.append(command)
        where = lambda: dest.get_text().strip() if baudelib.wire.is_dest(dest.get_text().strip()) else None  # noqa: E731

        def submit():
            if (to := where()) is None:
                raise ValueError("Where ssh goes: user@host, or an alias in ~/.ssh/config")
            network().add({"via": "ssh", "dest": to}, by_hand=True, command=command.get_text().strip() or None)
            toast(self.sidebar, f"Looking for Baude on {to}")
            return f"looking for Baude on {to}"
        return meta.Form("Add a host", box, [("destination", "user@host, or an alias in ~/.ssh/config", dest),
                                             ("command", "its host process's command there, if baude_headless.py "
                                                         "isn't on its PATH (or empty)", command)],
                         lambda: None if where() else "Where ssh goes: user@host, or an alias in ~/.ssh/config",
                         submit, verb="Add", body="Reached over ssh with your keys, never a password: the host must be "
                                                  "in known_hosts, and your key in its authorized_keys")

    def ask_add_host(self):
        """⋮ → Hosts → Add Host…: a host Baude doesn't find by itself (where multicast DNS doesn't reach, and not on
        Tailscale), as host_form has it."""
        form = self.host_form()
        dest, command = (w for _name, _takes, w in form.fields)
        dlg = Adw.AlertDialog(heading=form.heading, extra_child=form.child, default_response="add",
                              close_response="cancel", body=form.body)
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("add", form.verb)
        dlg.set_response_appearance("add", Adw.ResponseAppearance.SUGGESTED)
        dest.connect("changed", lambda *_: dlg.set_response_enabled("add", form.check() is None))
        dlg.set_response_enabled("add", False)
        dlg.connect("response", lambda _dlg, response: response == "add" and form.check() is None and form.submit())
        self.dialog = self.add_server_dialog = dlg
        dlg.present(self)
        dest.grab_focus()
        return dest, command

    def ask_host_command(self, key):
        """A host's right-click → Host Command…: the command its host process is started by there (ssh's
        `<command> connect`), if baude_headless.py isn't on its PATH or isn't the one; none: what it says, or PATH's."""
        r = next((r for r in network().remotes() if host_key(r) == key), None)
        if r is None:
            return
        entry = Gtk.Entry(text=r.command or "", activates_default=True, width_chars=36,
                          placeholder_text=r.where.get("command") or f"{baudelib.wire.SSH_COMMAND}, on its PATH")
        dlg = Adw.AlertDialog(heading=f"Baude on {remote_name(r)}", extra_child=entry, default_response="save",
                              close_response="cancel", body=f"The command ssh runs on {r.host}, with connect after it")
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("save", "Save")
        dlg.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        dlg.connect("response", lambda _d, res: res == "save" and network().set_command(r, entry.get_text().strip()))
        self.dialog = dlg
        dlg.present(self)
        entry.grab_focus()
        return entry

    def new_session_here(self, key):
        """⋮ → Hosts, a host right-clicked → New Session Here: a session started on it, through its server (this
        machine's: one started if there's none)."""
        if key == HERE_HOST:
            self.ask_remote_start(here=True)
        elif (r := next((r for r in network().alive() if host_key(r) == key), None)) is not None:
            self.ask_remote_start(server=r, fixed=True)
        else:
            toast(self.sidebar, "That host isn't there now")

    def ask_remote_start(self, server=None, folder=None, hosts=None, fixed=False, here=False):
        """New session through a server: the sidebar's New session while only other hosts are listed (hosts: their
        keys), a server's folder's +, or New Session Here (fixed: on server only; here: this machine's, one started
        there if there's none, local_server). A prompt, a folder and a group (the one group picked in ⋮, if one is);
        the model, effort and permission mode the top bar has (remote_start_form). Opened once its server's started it
        (remote_started)."""
        picked = self.sidebar.group_filter()
        group = next(iter(picked)) if picked is not None and len(picked) == 1 else None
        if here:
            servers = [r for r in network().alive() if host_key(r) == HERE_HOST][:1]
        elif fixed:
            servers = [server]
        else:
            servers = [r for r in network().alive(group) if hosts is None or host_key(r) in hosts]
            if server in servers:
                servers.remove(server)
                servers.insert(0, server)
            if not servers and (hosts is None or HERE_HOST in hosts):  # (none there: through this machine's, started)
                return self.ask_remote_start(folder=folder, here=True)
            if not servers:
                toast(self.sidebar, "None of those hosts is there")
                return
        form = self.remote_start_form(servers or [None], folder, group, said=here or fixed, here=here)
        dlg = Adw.AlertDialog(heading=form.heading, extra_child=form.child, default_response="start",
                              close_response="cancel", body=form.body)
        if hasattr(dlg, "set_prefer_wide_layout"):  # (libadwaita 1.6: room for a folder's whole path)
            dlg.set_prefer_wide_layout(True)
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("start", form.verb)
        dlg.set_response_appearance("start", Adw.ResponseAppearance.SUGGESTED)
        dlg.connect("response", lambda _dlg, response: response == "start" and self.submit_form(form))
        self.dialog = self.remote_dialog = dlg
        self.remote_fields = form.parts
        dlg.present(self)
        form.parts["prompt"].grab_focus()

    def submit_form(self, form):
        """A dialog's form submitted (its own button): what stops it said, as a toast, not raised in the handler."""
        try:
            form.submit()
        except ValueError as e:
            toast(self.sidebar, str(e))

    def remote_start_form(self, servers, folder=None, group=None, said=False, agents=False, here=False):
        """A new session's fields and what they do, New session…'s (ask_remote_start) and Meta's spawn_agent form's:
        the host (servers: those to pick from; None, this machine's host process, started as it's needed; said: the
        one, said, not picked; here: this machine's, as New Session Here has it), with agents what runs on it (the
        models it offers: its own, or the top bar's), the folder (the one last used on that host, else its home; empty:
        its home, ~), the group and the prompt; the effort and mode the top
        bar has, and its model unless an agent's picked. Started, it's opened (remote_started)."""
        grid = Gtk.Grid(row_spacing=8, column_spacing=12, width_request=420)
        rows = []

        def field(label, widget, top=False):
            name = Gtk.Label(label=label, xalign=1, valign=Gtk.Align.START if top else Gtk.Align.CENTER)
            name.add_css_class("dim-label")
            widget.set_hexpand(True)
            grid.attach(name, 0, len(rows), 1, 1)
            grid.attach(widget, 1, len(rows), 1, 1)
            rows.append(widget)
        named = lambda r: THIS_MACHINE if r is None or host_key(r) == HERE_HOST else \
            f"{remote_name(r)} ({r.host})"  # noqa: E731
        offered = lambda r: [(m["value"], m.get("displayName") or m["value"])  # noqa: E731
                             for m in (r.info.get("models") or [] if r is not None else [])] or MODELS
        servers_dd = agent_dd = None
        if said:  # (the host it's to be on: said, not picked)
            field("Host", Gtk.Label(label=named(servers[0]), xalign=0))
        else:
            servers_dd = Gtk.DropDown.new_from_strings([named(r) for r in servers])
            field("Host", servers_dd)
        server = lambda: servers[servers_dd.get_selected()] if servers_dd is not None else servers[0]  # noqa: E731
        if agents:
            agent_dd = Gtk.DropDown.new_from_strings([label for _value, label in offered(servers[0])])
            field("Agent", agent_dd)
        last = load_settings().get("remote_folders") or {}
        place = lambda r: HERE_HOST if r is None or host_key(r) == HERE_HOST else r.key  # noqa: E731
        home = lambda r: self.cwd if place(r) == HERE_HOST else r.info.get("home")  # noqa: E731
        where = Gtk.Entry(text=folder or last.get(place(servers[0])) or home(servers[0]) or "~")
        field("Folder", where)
        group_entry = Gtk.Entry(text=group or (servers[0].info.get("group") if servers[0] is not None else None) or "")
        field("Group", group_entry)
        prompt = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, accepts_tab=False, top_margin=6, bottom_margin=6,
                              left_margin=8, right_margin=8)
        frame = Gtk.ScrolledWindow(child=prompt, min_content_height=96, hscrollbar_policy=Gtk.PolicyType.NEVER)
        frame.add_css_class("card")
        field("Prompt", frame, top=True)
        effort = lambda: (self.local_effort if model_program(self.model) == "pi" else self.effort) \
            if self.effort_dd.get_sensitive() else None  # noqa: E731  (the top bar's as it's started)
        runs = " · ".join(x for x in (None if agents else self.model_dd.label(),
                                      self.effort_dd.label() if effort() else "", self.mode_dd.label()) if x)

        def picked_server(*_):  # (another server: the folder last used on it, else its home; what it runs)
            r = server()
            if not folder:
                where.set_text((load_settings().get("remote_folders") or {}).get(place(r)) or home(r) or "~")
            if agent_dd is not None:
                agent_dd.set_model(Gtk.StringList.new([label for _value, label in offered(r)]))
        if servers_dd is not None:
            servers_dd.connect("notify::selected", picked_server)

        def check():
            if agent_dd is not None and agent_dd.get_selected_item() is None:
                return f"{named(server())} offers no agent"
            return None

        def submit():
            if (problem := check()) is not None:
                raise ValueError(problem)
            buf = prompt.get_buffer()
            r = server()
            model = offered(r)[agent_dd.get_selected()][0] if agent_dd is not None else self.model
            params = {"op": "start", "prompt": buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True).strip(),
                      "cwd": where.get_text().strip() or "~", "model": model, "effort": effort(), "mode": self.mode,
                      "group": group_entry.get_text().strip() or None}
            save_settings(remote_folders={**(load_settings().get("remote_folders") or {}), place(r): params["cwd"]})
            start = lambda r: r.request(params, lambda reply: self.remote_started(r, reply))  # noqa: E731
            if r is None:
                self.local_server(start)
            else:
                start(r)
            return f"starting it on {named(r)}"
        on = THIS_MACHINE if here or servers[0] is None else remote_name(servers[0])
        heading = "Spawn an agent" if agents else f"New session on {on}" if said else "New session on the network"
        fields = ([("host", "the host it runs on", servers_dd)] if servers_dd is not None else []) + \
            ([("agent", "what runs it: one of the host's", agent_dd)] if agent_dd is not None else []) + \
            [("folder", "the folder it starts in, on that host", where), ("group", "its group (or empty)", group_entry),
             ("prompt", "what it's asked first", prompt)]
        form = meta.Form(heading, grid, fields, check, submit, verb="Start",
                         body=f"With the top bar's {runs}" if runs else "")
        form.parts = {"servers": servers_dd, "agent": agent_dd, "folder": where, "group": group_entry, "prompt": prompt}
        return form

    def group_form(self):
        """New Group's field and what it does, as the sidebar asks for one (ask_group_name), for Meta's new_group
        form."""
        entry = Gtk.Entry(activates_default=True, placeholder_text="Its name")
        name = lambda: " ".join(entry.get_text().split())  # noqa: E731

        def known():  # (the sidebar's, once the window has one: a form's drawn as the side panel is made, first)
            if (sidebar := getattr(self, "sidebar", None)) is not None:
                return {k for k, *_ in sidebar.known_groups()}
            return {*made_groups(), *chat_groups().values()} - {""}

        def check():
            if not name():
                return "A group needs a name"
            if name() in known():
                return f"There's a group {name()} already"
            return None

        def submit():
            if (problem := check()) is not None:
                raise ValueError(problem)
            self.sidebar.make_group(name())
            return f"made group {name()}"
        return meta.Form("New group", entry, [("name", "the group's name", entry)], check, submit, verb="Create",
                         body="A list of chats of its own, picked in ⋮ → Groups; a chat's right-click → Move to Group "
                              "puts it there")

    def check_host_version(self):
        """This Mac's host process older than Baude (an update since it started: its protocol, wire.VERSION, behind):
        said, once a host process, with Restart It (restart_host)."""
        r = local_host()
        if r is None or (r.info.get("version") or 1) >= baudelib.wire.VERSION or \
                _HOST_SAID.get("old") == r.info.get("pid"):
            return
        _HOST_SAID["old"] = r.info.get("pid")
        t = Adw.Toast(title=f"{THIS_MACHINE}'s host process is older than this Baude", button_label="Restart It",
                      timeout=0)
        t.connect("button-clicked", lambda *_: self.restart_host())
        self.toasts.add_toast(t)

    def restart_host(self, waited=0):
        """This Mac's host process stopped and started again (a newer one, after an update): once no command's running
        there (the host says: quit if_idle), as stopping it would stop that; a turn that's only the model writing is
        cut short, said so (cut), and carried on with Continue. Its claudes stop, their transcripts kept, and each of
        its sessions resumes as it's next written to (the windows on them reattach as it's back)."""
        r = local_host()
        if r is None:
            return
        busy = {c.get("session") for c in (r.info.get("update") or {}).get("waiting") or ()} - {None}  # (its say)

        def later(n):
            if not waited:
                toast(self.sidebar, f"Restarting {THIS_MACHINE}'s host process once {n} session"
                                    f"{'s finish' if n > 1 else ' finishes'} running a command")
            GLib.timeout_add_seconds(5, lambda: self.restart_host(waited + 5) and False)

        def answered(reply):
            if reply.get("op") == "busy":
                return later(len(reply.get("sessions") or []) or 1)
            end = time.monotonic() + 15

            def gone():  # (the old one gone: a new one, started as any is: local_server)
                if r.state == "alive" and time.monotonic() < end:
                    return True
                self.local_server(lambda _r: toast(self.sidebar, f"{THIS_MACHINE}'s host process restarted"))
                return False
            GLib.timeout_add(250, gone)
        if busy:
            return later(len(busy))
        r.request({"op": "quit", "if_idle": True}, answered)

    def local_server(self, then, wait_s=20, quiet=False):
        """This machine's host process (local_host), which runs every chat of Baude's: then(it), on the main loop, once
        it's there. The one that's there, else one started (baudelib.hosts.start_here: baude_headless.py, in a process
        of its own that goes on after Baude does, its output in its state folder's host.log; one running whose socket's
        gone, told to listen again). Waited for once, for every window that wants it. quiet: nothing said of it."""
        net = network()
        if (r := local_host()) is not None:
            then(r)
            return
        if _LOCAL_SERVER["wanted"] is not None:
            _LOCAL_SERVER["wanted"].append(then)
            return
        # (as the command-line heads start it: one that's running but lost its socket nudged)
        threading.Thread(target=baudelib.hosts.start_here, daemon=True, name="start-host").start()
        if not quiet:
            toast(self.sidebar, f"Starting {THIS_MACHINE}'s host process…")
        _LOCAL_SERVER["wanted"] = [then]
        end = time.monotonic() + wait_s

        def look():
            net.here()  # (its socket's there as it binds: no need to wait for the next look)
            net.look()
            r = local_host()
            if r is None and time.monotonic() < end:
                return True
            wanted, _LOCAL_SERVER["wanted"] = _LOCAL_SERVER["wanted"], None
            if r is None:
                self.note(f"{THIS_MACHINE}'s host process didn't answer: {pretty_path(baudelib.hosts.log_path())} "
                          "may say why")
            for fn in wanted:
                r is not None and fn(r)
            return False
        GLib.timeout_add(250, look)

    def remote_new_conversation(self, model, label):
        """Another kind of model picked in a network session (Claude's to Apple's, say): as in a chat here, a new
        conversation, but on the same server, in the same folder and group, opened to be written in."""
        r, listed = self.remote, self.remote.sessions.get(self.session_id) or {}
        effort = self.local_effort if model_program(model) == "pi" else self.effort
        self.note(f"model → {label}: a new conversation on {remote_name(r)}")
        r.request({"op": "start", "prompt": "", "cwd": listed.get("cwd") or "~", "model": model, "effort": effort,
                   "mode": self.mode, "group": listed.get("group"), "draft": True,
                   **({"env": claudelib.claude_env()} if r.mine else {})},
                  lambda reply: self.remote_started(r, reply))

    def remote_started(self, r, reply):
        """A server started a session asked for here: its host and group listed, and it opened."""
        if reply.get("op") != "started":
            self.note(f"{remote_name(r)} didn't start it: {reply.get('error')}")
            return
        r.sessions[reply["session"]["id"]] = reply["session"]  # (listed before the server next says what it has)
        sb, info = self.sidebar, remote_info({**reply["session"], "server": r})
        hosts, groups, group = sb.host_filter(), sb.group_filter(), chat_group(info)  # (listed, if ⋮ leaves them out)
        sb.filtered(sidebar_hosts=sorted(hosts | {host_key(r)}) if hosts is not None else None,
                    sidebar_groups=sorted(groups | {group}) if groups is not None else None)
        self.open_remote_soon(info)

    def open_remote_link(self, uri):
        """A link in a network session to a file on its server, not this machine: fetched from it and shown in a tab, as
        a file here is (show_code). False for anything else (a web address, a session on this machine)."""
        r = self.remote
        if r is None or baudelib.client.is_here(r.info) or \
                re.match(r"[a-z][a-z0-9+.-]*:", uri, re.I) and not uri.startswith("file:"):
            return False
        m = re.fullmatch(r"(?:file://)?(.+?)(?:#L?(\d+)(?:-L?\d+)?|:(\d+)(?::\d+)?)?", uri)
        if not m:
            return False
        home = r.info.get("home") or "/"
        raw = urllib.parse.unquote(m[1])
        raw = home + raw[1:] if raw == "~" or raw.startswith("~/") else raw
        cwd = (r.sessions.get(self.session_id) or {}).get("cwd") or home
        path, line = os.path.normpath(os.path.join(cwd, raw)), int(m[2] or m[3]) if m[2] or m[3] else None

        def shown(reply):
            data = base64.b64decode(reply.get("data") or "") if reply.get("op") == "file" else b""
            if reply.get("op") != "file" or reply.get("error") or b"\0" in data[:8192]:
                self.note(f"{path} on {remote_name(r)}: {reply.get('error') or 'not text to read here'}")
                return
            self.show_code(path, line, text=data.decode("utf-8", "replace"), note=f"on {remote_name(r)}")
        r.request({"op": "read_file", "path": path}, shown)
        return True

    def show_sessions(self):
        self.sidebar_slide.set_shown(True)
        self.sidebar.focus_search()

    def resume_session(self, info):
        if info.get("remote") is not None:
            return self.open_remote(info)
        self.remember_own_writes()  # before the view switches to the other session
        if self.busy and self.proc is not None:
            self.detach_proc()  # (the turn goes on: detach_proc; a host's chat's let go of, before it's not this one)
        self.remote, self.desktop_chat = None, bool(info.get("desktop")) or is_desktop(info.get("id"))
        MarkdownView.base_dir = self.run_cwd  # (not a network session's folder, whatever's set below)
        cwd = info.get("cwd")
        self.clear_transcript()
        self.switching_chat = True  # (its own switches come below: not the chat before's, in its folder)
        if cwd and os.path.isdir(cwd):  # (where it was recorded, a worktree too: --resume finds it in that folder's
            self.set_cwd(cwd)           # project, so claude runs there; the window's folder is its repository's)
        elif cwd:
            if (folder := main_folder(cwd)[0]) != cwd and os.path.isdir(folder):
                self.set_cwd(folder)  # (a worktree that's gone: its repository)
            self.note(f"{pretty_path(cwd)} no longer exists; resuming in {self.short_cwd()}")
        self.reset_stream_state()
        self.session_id = info["id"]  # (before the history: Signal files its replies under it)
        self.apply_chat_model(info)  # (the model it was on, not the chat before's)
        self.signal_pending, self.switching_chat = None, False
        tree = main_folder(self.run_cwd)[1]  # (the checkout it works in, before Signal starts: from what it did, else
        self.ref_checkout = chat_checkout(self.cwd, info["path"]) or (tree if tree and os.path.isdir(tree) else None)  # the worktree it runs in)
        self.apply_chat_signal()  # (this chat's own switches)
        self.follow_checkout(info["path"])
        self.view_session(info["path"])
        if info.get("title"):
            self.show_title(info["title"])
        self.read_only_mode()
        if (left := self.take_background(info["id"])) is not None:  # (left mid-turn, still at it: its own claude again)
            self.attach_proc(left)
        else:
            self.restart_proc(resume=True)  # a fresh helper: resume_pending keeps --resume off
        self.apply_read_only()
        self.sidebar.fill()

    def view_session(self, path):
        """Show a saved session read-only and follow it as it grows (it may be running elsewhere)."""
        try:
            st = os.stat(path)
        except OSError:
            st = None
        live = st is not None and time.time() - st.st_mtime < LIVE_SECONDS
        self.total_cost = session_cost(path)  # (claude's, as it last exited)
        self.load_history(path, live=live)
        self.resume_pending, self.view_path = True, path
        self.tail.start(path, st.st_size if st else 0)
        if st is not None and st.st_mtime <= self.own_mtimes.get(path, -1) + 1:
            self.tail.last_written, self.tail.turn_open = 0.0, False  # baude's own writes, not another program's
        self.update_banner()
        self.follow_checkout(path)

    def recorded_cost(self, session_id):
        """What the session's transcript says claude had spent on it when it last exited (its last cost-state), which a
        claude --resume'd on it counts on from; 0 for none."""
        info = cached_session_info(path) if session_id and (path := session_file(session_id)) else None
        return (info or {}).get("cost") or 0.0

    def remember_own_writes(self):
        """Note how baude's own claude left the session it ran, so coming back to it isn't
        mistaken for another program having just written it."""
        if self.resume_pending or not self.session_id:
            return
        if (info := find_session(self.session_id)) is not None:
            try:
                self.own_mtimes[info["path"]] = os.stat(info["path"]).st_mtime
            except OSError:
                pass

    def live_elsewhere(self):
        """Is the viewed session one Claude Desktop runs, and is it writing it now? Desktop runs its chats itself, so a
        second writer here would interleave two conversations in its transcript: that's kept from it (update_banner).
        No other chat is: a host's session (this machine's or another's), one this machine's host hasn't taken up yet,
        one another window or a terminal's claude has open too. Several writers at once is how those are used, and
        what's sent mid-turn waits for the turn's end. Not Baude's own claude at a turn in a Desktop chat left
        mid-turn, which writing here takes over (begin_resume)."""
        if not self.resume_pending or self.remote is not None:
            return False
        if not (self.desktop_chat or is_desktop(self.session_id)):
            return False
        return self.tail.in_use() and background_proc(self.session_id) is None

    def update_banner(self):
        """While another program (not Baude: live_elsewhere) has the session, or a host's session's host isn't there,
        say so, with no text box (show_composer): two writers would interleave two conversations in one transcript, and
        a host that's gone has no one to send to."""
        live = self.live_elsewhere()
        gone = self.remote is not None and self.resume_pending and self.remote.state != "alive"
        if gone:
            name = remote_name(self.remote)
            self.banner.set_title(f"{name} isn't on the network: this session is as it was last seen"
                                  if self.remote.state == "dead" else f"Looking for {name} on the network…")
        elif live:
            self.banner.set_title("This session is currently active elsewhere")
        self.banner.set_button_label(None)
        self.banner.set_revealed(live or gone)
        self.locked = live or gone
        self.show_composer()

    def tail_lines(self, lines):
        """Draw what other programs appended to the viewed session."""
        if not (self.resume_pending and self.view_path):
            return
        items = history_items(lines)
        if items:
            if self.history is not None:
                self.history.shown += len(items)
            self.render_history(items, final=False)
        self.update_banner()
        self.update_session_state()
        self.follow_checkout(self.view_path)

    def poll_tail(self):
        """Once a second: let 'live' and 'working' expire when the writes stop."""
        if self.resume_pending and self.view_path:
            self.read_subagents()
            self.update_banner()
            self.update_session_state()
        return True

    def begin_resume(self):
        """The first message: stop viewing, start `claude --resume` on the session."""
        if self.remote is not None and self.remote.state == "alive" and self.view_path:
            # (a network session: what its server's written since it was last fetched, drawn now, not lost to the
            # live chat that takes over: the end of a turn answered while it was watched, say)
            self.remote.sync(self.session_id, timeout=3, subagents=False)  # (on the main loop: only what's drawn)
            self.tail.read()
        self.resume_pending, self.view_path = False, None
        self.tail.stop()
        self.update_banner()
        if self.resumed_note:
            self.note("— resumed —")
            self.resumed_note = False
        if (left := self.take_background(self.session_id)) is not None:  # (left mid-turn in a window, still at it)
            self.attach_proc(left)
            return
        self.restart_proc(resume=True)

    def h_result(self, msg):
        GLib.idle_add(lambda: self.follow_checkout() and False)  # (the turn may have moved into a worktree)
        if self.proc is not None:
            self.proc.asks.clear()  # (the turn's over: what it asked and wasn't answered can't be now)
        if self.proc is not None and not hasattr(self.proc, "cost_seen"):  # (what --resume counted on from, read now,
            self.proc.cost_seen = self.recorded_cost(getattr(self.proc, "resumed", None))  # not as it started: the
        self.total_cost += spent_since(self.proc, msg)  # claude before writes its cost-state as it exits, after that)
        self.blocks.clear()
        u = msg.get("usage", {})
        tok = (u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0)
               + u.get("cache_creation_input_tokens", 0))
        if msg.get("is_error"):
            self.note(f"turn ended with error: {msg.get('result') or msg.get('subtype')}")
            if needs_login(msg.get("result")):
                self.login_prompt(msg["result"])
        self.set_busy(False)
        self.keep_chat_model()
        if getattr(self, "restart_after_turn", False):  # (signed in while it ran: on the new sign-in now)
            self.restart_after_turn = False
            GLib.idle_add(lambda: self.restart_claude() and False)
        self.refresh_context()
        self.sidebar.refresh()  # the session file just changed (first save, newer time)
        self.update_status(f"{msg.get('num_turns', 0)} turns · {msg.get('duration_ms', 0) / 1000:.1f}s"
                           f" · in {tok} / out {u.get('output_tokens', 0)} tok")

    def h_control_response(self, msg):
        r = msg.get("response", {})
        callback, refused = self.pending.pop(r.get("request_id"), (None, None))
        if r.get("request_id") == self.proc.init_rid and r.get("subtype") == "success":
            trace("claude ready")  # (its commands, models and modes are known)
            resp = r.get("response") or {}
            self.completer.set_commands(resp.get("commands") or [])
            self.sync_models(resp.get("models"))
            self.sync_mode(resp.get("current_permission_mode"))
            self.refresh_effort()
            self.refresh_context()
        elif callback and r.get("subtype") == "success":
            callback(r.get("response") or {})
        elif r.get("subtype") == "error" and refused:
            refused(r.get("error") or "")
        elif r.get("subtype") == "error" and not callback:
            self.note(f"control error: {r.get('error')}")

    def h_control_request(self, msg):
        rid, req = msg["request_id"], msg.get("request", {})
        if req.get("subtype") != "can_use_tool":
            self.proc.send({"type": "control_response", "response": {
                "subtype": "error", "request_id": rid, "error": f"baude: unsupported {req.get('subtype')}"}})
            return
        name, inp = req.get("tool_name"), req.get("input", {})
        if name in self.always_allow and model_program(self.model) == "claude":  # (a model on this Mac: always asked)
            self.proc.asks.pop(rid, None)  # (one held while its chat was left: answered now)
            self.proc.respond(rid, {"behavior": "allow", "updatedInput": inp})
            return
        self.proc.asks[rid] = msg  # (asked again here if the chat's left and opened again before it's answered)
        self.permission_card(rid, name, inp, req.get("tool_use_id"))

    def sign_in(self, args=()):
        """/login (or Sign in on a turn that failed for want of it): claude auth login, as a card (LoginCard)."""
        self.add(LoginCard(self, args))
        self.stick_bottom = True

    def signed_in(self):
        """Signed in afresh: the chat's claude started again on it (after the turn, if one's running)."""
        if self.busy:
            self.restart_after_turn = True
        else:
            self.restart_claude()

    def restart_claude(self):
        """The chat's claude started again (signed in or out since): Baude's own, here (restart_proc); this machine's
        host process's, stopped there, so its next message starts it again, as it's signed in now."""
        if self.remote is not None and self.remote.mine and self.session_id:
            self.remote.request({"op": "stop", "session": self.session_id, "restart": True})
        self.restart_proc(resume=True)

    def sign_out(self):
        """/logout: claude auth logout, said in the chat; the chat's claude started again (signed out, as it is)."""
        self.note("signing out…")

        def run():
            try:
                r = subprocess.run([claude_exe(), "auth", "logout"], capture_output=True, text=True, timeout=60,
                                   stdin=subprocess.DEVNULL, env=claude_env())
                said = _AUTH_ESCAPES.sub("", (r.stdout + r.stderr)).strip().splitlines()
                text = "signed out of Claude" if r.returncode == 0 else \
                    f"sign-out failed: {said[-1] if said else r.returncode}"
            except (OSError, subprocess.SubprocessError) as e:
                text = f"sign-out failed: {e}"
            GLib.idle_add(lambda: (self.note(text), self.signed_in()) and False)
        threading.Thread(target=run, daemon=True).start()

    def login_prompt(self, why):
        """A turn that failed for want of signing in: said, with Sign in (sign_in); another machine's: where to."""
        if self.remote is not None and not self.remote.local:
            self.note(f"{remote_name(self.remote)}'s claude isn't signed in: run claude auth login on it")
            return
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        card.add_css_class("perm-card")
        card.append(sel_label("<b>Claude Code isn't signed in</b>  <span alpha='60%'>"
                              f"{html.escape(why[:200])}</span>"))
        btn = Gtk.Button(label="Sign in", halign=Gtk.Align.START)
        btn.add_css_class("suggested-action")
        btn.connect("clicked", lambda *_: (card.get_parent() and card.get_parent().remove(card), self.sign_in()))
        card.append(btn)
        self.add(card)
        self.stick_bottom = True

    def permission_card(self, rid, name, inp, tool_use_id):
        """Asks whether to allow a call: in the call's own card, when it's in the chat (ToolCard.ask); else (no card for
        it yet, or it's a subagent's, drawn elsewhere) in a box of its own here, saying what it would run."""
        card = self.tool_cards.get(tool_use_id)
        if card is not None and tool_use_id in self.tool_parent:
            card = None
        row = Gtk.Box(spacing=6)
        box = None
        if card is None:
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            box.add_css_class("perm-card")
            title, source = tool_display(name)
            what = f"{source}: {title}" if source else title
            summary = summarize_tool(name, inp)
            box.append(sel_label(f"<b>Allow {html.escape(what)}?</b>" +
                                 (f"  <span alpha='60%'>{html.escape(summary)}</span>" if summary else "")))
            box.append(tool_input_view(name, inp))
            box.append(row)
        waiting = card if card is not None else box
        self.waiting_cards.add(waiting)
        self.update_session_state()
        proc = self.proc

        def decide(_b, behavior, always=False):
            if always:
                self.always_allow.add(name)
                if self.remote is not None and self.session_id:  # (its host allows it too, a head attached or not)
                    self.remote.request({"op": "always_allow", "session": self.session_id,
                                         "tools": sorted(self.always_allow)})
            proc.asks.pop(rid, None)
            if behavior == "allow":
                proc.respond(rid, {"behavior": "allow", "updatedInput": inp})
            else:
                proc.respond(rid, {"behavior": "deny", "message": "The user denied this tool use."})
            if card is not None:
                card.answered()
            else:
                box.get_parent() and box.get_parent().remove(box)
            self.waiting_cards.discard(waiting)
            self.update_session_state()
            decision = ("always allowed" if always else "allowed") if behavior == "allow" else "denied"
            if tool_use_id in self.tool_cards:
                self.tool_cards[tool_use_id].set_decision(decision)  # keeps tool groups together
            elif behavior != "allow":  # (allowed, it runs: nothing to say)
                self.note(f"{decision} {name}")

        choices = [("Allow", "allow", False, "suggested-action"),
                   (f"Always allow {tool_display(name)[0]}", "allow", True, None),
                   ("Deny", "deny", False, "destructive-action")]
        if model_program(self.model) != "claude":  # (Apple's model, or one on this Mac: each call asked, no always)
            del choices[1]
        for label, beh, always, css in choices:
            b = Gtk.Button(label=label)
            if css:
                b.add_css_class(css)
            b.connect("clicked", decide, beh, always)
            row.append(b)
        if card is not None:
            # (however it stops asking, answered or not, it isn't waiting on you any more; the window held weakly, as the
            # card's listeners outlive its handlers)
            me = weakref.ref(self)
            card.listeners.append(lambda c: c.asking is None and (proc.asks.pop(rid, None), True) and (w := me()) is not None
                                  and c in w.waiting_cards and (w.waiting_cards.discard(c), w.update_session_state()))
            card.ask(row)
        else:
            self.add(box)
        self.stick_bottom = True


class BaudeApp(Adw.Application):
    def __init__(self, opts):
        # a traced run (a benchmark) is its own app: never handed to the baude you're using
        super().__init__(application_id=APP_ID,
                         flags=Gio.ApplicationFlags.NON_UNIQUE if TRACE else Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.opts = opts
        self.updater = None  # (update.Updater: started with the first window)

    def do_shutdown(self):
        save_info_cache(force=True)
        if _NETWORK:
            _NETWORK[0].save()  # (the network's sessions as last seen: dead ones are shown from it next time)
        Adw.Application.do_shutdown(self)

    def do_startup(self):
        Adw.Application.do_startup(self)
        GLib.set_application_name("Baude")
        if sys.platform == "darwin":
            for fn in (macos_dock_icon, macos_appearance):
                try:
                    fn()
                except Exception as e:  # (Python's Dock tile isn't worth not starting over)
                    print(f"baude: {fn.__name__}:", e, file=sys.stderr)
        pointers.start()
        install_debug_hooks(self)

    def do_activate(self):
        trace("activate")
        Gtk.Window.set_default_icon_name(APP_ID)
        prov = Gtk.CssProvider()
        prov.load_from_string(CSS)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), prov,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        restore, self.opts.restore = take_restart_state(getattr(self.opts, "restore", None)), None  # (once)
        if restore is not None and "windows" not in restore:
            restore = {"windows": [restore]}  # (one window's, from before every window came back)
        for i, state in enumerate((restore or {}).get("windows") or [None]):
            opts = argparse.Namespace(**vars(self.opts))
            if state:
                opts.cwd, opts.restore = state.get("cwd") or opts.cwd, state
            win = BaudeWindow(self, opts)
            trace("window built")
            if TRACE and i == 0:
                win.connect("map", lambda *_: trace("window mapped"))
                clock_id = []

                def painted(clock):
                    trace("first frame")
                    clock.disconnect(clock_id[0])
                win.connect("realize", lambda w: clock_id.append(
                    w.get_frame_clock().connect("after-paint", painted)))
            update.show(win, restore)  # (in the order they were in: the last one you were in, last, on top)
        if restore:
            update.restarted(win, restore, "Baude")
        if self.updater is None and not TRACE:
            self.updater = update.Updater("baude", self.idle, self.restart, blocked=self.update_waiting)
        # (whether this claude knows --chrome, looked up now, off the main loop: a chat starts without waiting on it)
        threading.Thread(target=lambda: claudelib.has_flag(claudelib.claude_exe(), "--chrome"), daemon=True).start()

    def windows(self):
        """Baude's windows, the one you were in last first."""
        return [w for w in self.get_windows() if isinstance(w, BaudeWindow)]

    def idle(self):
        """Every window idle (BaudeWindow.idle): a new version can be restarted onto."""
        return all(w.idle() for w in self.windows())

    # ---- Update Available: the sidebars' foot while a new version waits, Baude's (on its windows: restart_blockers)
    # or This Mac's host process's (on its commands: info's update)
    def update_waiting(self, waiting):
        self.update_held = waiting
        self.show_update_bars()

    def update_reasons(self):
        """What the updates waiting wait for, said; None if none is waiting."""
        why, waiting = [], False
        if getattr(self, "update_held", False) and self.updater is not None and self.updater.ready:
            waiting = True
            why += [w for win in self.windows() for w in win.restart_blockers()]
        host = local_host()
        held = (host.info.get("update") if host is not None else None) or None
        if held:
            waiting = True
            why += [f"{THIS_MACHINE}'s host process: {c.get('what') or 'a command'}"
                    for c in held.get("waiting") or []] or [f"{THIS_MACHINE}'s host process"]
        return list(dict.fromkeys(why)) if waiting else None

    def show_update_bars(self):
        why = self.update_reasons()
        for w in self.windows():
            if (bar := getattr(w.sidebar, "update_bar", None)) is not None:
                bar.say(why)

    def update_now(self, win):
        """The bar clicked: restarted now, what's running stopped (said first, and asked)."""
        why = [w for w in (self.update_reasons() or []) if not w.startswith("you, using")]
        dlg = Adw.AlertDialog(heading="Restart now?", default_response="cancel", close_response="cancel",
                              body="The new version is picked up now. "
                                   + ("These are stopped, and their chats can be continued after:\n"
                                      + "\n".join(f"• {w}" for w in why) if why else "Nothing's running."))
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("go", "Restart Now")
        dlg.set_response_appearance("go", Adw.ResponseAppearance.DESTRUCTIVE if why else Adw.ResponseAppearance.SUGGESTED)

        def answered(_d, r):
            if r != "go":
                return
            host = local_host()
            if host is not None and host.info.get("update"):
                host.request({"op": "quit"})  # (it comes back as a chat wants it: local_server)
            if self.updater is not None and self.updater.ready:
                u = self.updater
                self.restart({"from": u.started, "to": u.ready, "commits": update.new_commits(u.started, u.ready, u.root)})
        dlg.connect("response", answered)
        dlg.present(win)

    def restart(self, updated=None):
        """This process replaced by a fresh baude (a new version picked up) that puts back every window as it was
        (restart_state), giving the front back to the app you were in (update.restarted): ☰ → Restart Baude, or a
        new version found (update.Updater; updated, what by). A session being watched isn't touched, and one of
        baude's own is resumed after (a turn it was running is cut short, as Ctrl+C would)."""
        wins = self.windows()[::-1]  # (the one you were in last, last: put back last, on top)
        path = restart_state_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump({"windows": [w.restart_state() for w in wins], "front": update.front_app(), "updated": updated}, f)
        for w in wins:
            if w.proc:
                w.proc.on_exit = lambda *_: None
                w.proc.kill()
            for p in list(w.background.values()):  # (chats left mid-turn: their turns end here)
                w.let_go_background(p)
        save_info_cache(force=True)  # (as quitting does: exec skips do_shutdown)
        if _NETWORK:
            _NETWORK[0].save()
        os.execv(sys.executable, launch_command("--cwd", wins[-1].run_cwd if wins else self.opts.cwd, "--restore", path))


def debug_report_path():
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return os.path.join(base, "baude", f"debug-{os.getpid()}.txt")


def window_state(win):
    """What a window is doing, for a debug report: enough to tell a stuck UI from a locked session."""
    now, proc = time.time(), getattr(win, "proc", None)
    alive = proc is not None and proc.p.poll() is None
    try:
        live = win.live_elsewhere()
    except Exception as e:  # noqa: BLE001  (a report must not fail)
        live = f"error: {e}"
    last = win.tail.last_written or 0
    history = getattr(win, "history", None)
    return {
        "title": win.get_title(), "session_id": win.session_id, "cwd": win.cwd, "run_cwd": win.run_cwd,
        "history_path": getattr(win, "history_path", None),
        "viewing read-only (resume_pending)": getattr(win, "resume_pending", None),
        "locked: live elsewhere": live,
        "other program last wrote, s ago": round(now - last, 1) if last else None,
        "its turn open (tail.turn_open)": win.tail.turn_open,
        "banner shown": win.banner.get_revealed(), "text box shown": win.composer.get_visible(),
        "busy": win.busy, "stick_bottom": win.stick_bottom,
        "claude": {"pid": proc.p.pid if proc else None, "alive": alive,
                   "args": proc.p.args if proc else None, "exit code": None if alive or not proc else proc.p.poll()},
        "pending control requests": list(win.pending),
        "tasks": {k: {f: t.get(f) for f in ("task_type", "status", "description")} for k, t in win.tasks.items()},
        "history items not drawn yet": history.count() if history else None,
        "tool cards": len(win.tool_cards), "zoom": win.zoom,
    }


def install_debug_hooks(app):
    """Inspect a running Baude without a debugger (ptrace is usually off limits):
       kill -USR1 <pid>   every thread's Python stack, written from the signal handler itself, so it
                          comes even when the main thread is stuck
       kill -USR2 <pid>   each window's state (session, lock, busy, claude, tasks), from the main loop
    Both append to ~/.cache/baude/debug-<pid>.txt."""
    import faulthandler
    import signal
    path = debug_report_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        report = open(path, "a", buffering=1)  # (kept open: faulthandler writes to its descriptor)
    except OSError:
        return None
    faulthandler.register(signal.SIGUSR1, file=report, all_threads=True)

    def state():
        report.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')}  Baude state (pid {os.getpid()})\n")
        for win in app.get_windows():
            if isinstance(win, BaudeWindow):
                report.write(json.dumps(window_state(win), indent=2, default=str) + "\n")
        return True  # (keep listening)
    try:
        gi.require_version("GLibUnix", "2.0")
        from gi.repository import GLibUnix
        GLibUnix.signal_add(GLib.PRIORITY_HIGH, signal.SIGUSR2, state)
    except (ImportError, ValueError):  # older GLib
        GLib.unix_signal_add(GLib.PRIORITY_HIGH, signal.SIGUSR2, state)
    return path


def main():
    ap = argparse.ArgumentParser(prog="Baude", description=__doc__.splitlines()[0])
    ap.add_argument("--cwd", default=os.getcwd())
    ap.add_argument("--model", help="alias (opus, sonnet, ...) or full model name; default: the CLI's")
    ap.add_argument("--resume", help="session id to resume")
    ap.add_argument("--restore", help=argparse.SUPPRESS)  # state left by "Restart Baude"
    inst = ap.add_mutually_exclusive_group()
    inst.add_argument("--install", action="store_true",
                      help=f"add Baude to your {LAUNCHER.where} (per user) and exit")
    inst.add_argument("--uninstall", action="store_true",
                      help=f"remove Baude from your {LAUNCHER.where} and exit")
    opts = ap.parse_args()
    if opts.install:
        LAUNCHER.install()
        print("\n".join(f"installed {p}" for p in LAUNCHER.paths()))
        return 0
    if opts.uninstall:
        removed = LAUNCHER.uninstall()
        print("\n".join(f"removed {p}" for p in removed) or "Baude was not installed")
        return 0
    return BaudeApp(opts).run([sys.argv[0]])


if __name__ == "__main__":
    sys.exit(main())
