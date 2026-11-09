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
Ctrl+. (⌘.) Send Feedback over whatever's open.
Images and PDFs can also be attached with the paperclip or dropped onto the window.

Set BAUDE_CLAUDE=/path/to/claude to use a specific claude binary (else claude on PATH, else, on macOS,
the Claude desktop app's).
"""
import argparse
import array
import base64
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

import lookup
from macos import full_redraws, ns_string, objc_send  # (no GTK: used before it loads)
from lookup import (  # (⌘K's engine: the names a folder has; see lookup.py)
    CATALOGUE_TTL, GITHUB, Names, PG_LISTS, REF_KINDS, REF_LOC_FIELDS, Ref, _CATALOGUE_TOKEN, _KUBE_ACTION, _KUBE_NAME,
    _git, _km, _ks, _kube_args, arango_eval, at_once, catalogue_hits, catalogue_ref, catalogue_shaped, db_index,
    db_names, find_spans, git_exe, github_data, kube_contexts, kube_fault, kube_fmt_age, kube_get, kube_local, kube_made_as,
    kube_run, kube_running, kube_seconds, kube_tool, kubeconfig_paths, pg_databases, pg_databases_cached, pg_query,
    pg_servers, pg_servers_cached, ref_date, ref_icon, ref_key, ref_palette_rank, ref_yaml_path,
    signal_glossary_exists, signal_place, signal_store,
)


def macos_fonts():
    """On macOS, text through FreeType rather than CoreText (before Pango is loaded). Pango's CoreText path puts
    glyphs on whole pixels, so letters are spaced unevenly and look thin; FontConfig + FreeType2 places them as on
    Linux (https://discourse.gnome.org/t/font-antialiasing-issue-on-gtk-in-macos/28071). FontConfig's own sans-serif
    and monospace are Verdana and Andale Mono there: a config of baude's own (not yours) makes them the system's
    font and Menlo. PANGOCAIRO_BACKEND or FONTCONFIG_FILE set already: left as they are."""
    if sys.platform != "darwin" or "PANGOCAIRO_BACKEND" in os.environ or "FONTCONFIG_FILE" in os.environ:
        return
    main = next((p for p in ("/opt/homebrew/etc/fonts/fonts.conf", "/usr/local/etc/fonts/fonts.conf")
                 if os.path.exists(p)), None)
    if main is None:  # (no FontConfig: CoreText it is)
        return
    alias = '  <alias binding="strong"><family>{}</family><prefer><family>{}</family></prefer></alias>\n'
    conf = ('<?xml version="1.0"?>\n<!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd">\n<fontconfig>\n'
            f'  <include ignore_missing="yes">{main}</include>\n'
            + alias.format("sans-serif", "System Font") + alias.format(".AppleSystemUIFont", "System Font")
            + alias.format("monospace", "Menlo") + '</fontconfig>\n')
    folder = os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "baude")
    path = os.path.join(folder, "fonts.conf")
    try:
        os.makedirs(folder, exist_ok=True)
        with open(path, "w") as f:
            f.write(conf)
    except OSError:
        return
    os.environ["PANGOCAIRO_BACKEND"], os.environ["FONTCONFIG_FILE"] = "fc", path


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
from fixes import HOST_SESSION_ENV  # noqa: E402
import fixview  # noqa: E402  (☰ → Ongoing Feedback: the fixes, approved or rejected)
import livefeed  # noqa: E402  (a chat followed as it's written: Record's too)
import pointers  # noqa: E402  (the pointer's shape: Record's too)
from livefeed import (  # noqa: E402
    LIVE_SECONDS, TURN_OPEN_SECONDS, external_state, read_appended, transcript_turn_open)
import update  # noqa: E402  (a new version from git, restarted onto once idle: Record's too)
from feedback import (  # noqa: E402,F401
    Capture, FEEDBACK_SAVE_KEYS, FeedbackDialog, FeedbackDraft, FeedbackHost, RegionEditor, RegionLayer, _visible_rect, capture_window, feedback_dir, feedback_items, save_feedback,
)
import widgets  # noqa: E402  (what Record draws as Baude does: the links' logos; see widgets.py)
from widgets import hover_uri, markup_links  # noqa: E402,F401  (a label's links, by where the pointer is)
from widgets import REF_ICON_SVG, RefIcon, draw_ref_icon, ref_icon_ops  # noqa: E402,F401
from widgets import HEADING_SCALE, Composer, ComposerMarkdown, ComposerView  # noqa: E402,F401
from widgets import (  # noqa: E402,F401
    _data_home, SCALE_MAX, SCALE_MIN, SETTINGS_KEY, settings_size, settings_switch, ZOOM_MODS, macos_appearance, scale_setting, zoom_keys,
    CSS, PALETTE_KEY, RefPaletteRow, TreeList, load_css, sidebar_action, ARTIFACT_TITLES, BAUDE_ICONS, BAUDE_SHAPES, BULLET, CODE_STYLES, CodeBlock,
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
    file_children, file_icon, git_ignored, python_context,
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
    GIT_LOG_LIMIT, GIT_MERGE_LIMIT, GIT_OFF_PEAK, GIT_POOL, GitDelta, GitHours,
    GitLane, GitPage, _CHAT_GIT, _GH_STATE, _GIT_COMMIT_OUT, _GIT_FMT,
    _GIT_PUSH_OUT, _gh_json, chat_git_events, gh_state, git_ahead_behind, git_chat_commit,
    git_checkouts, git_commit_card, git_delta_markup, git_find, git_full_when, git_hours,
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
# (value, label). MODELS is only a fallback: the CLI's initialize response lists the real
# models, and reports the permission mode it actually started in.
MODELS = [("opus", "Opus"), ("fable", "Fable"), ("sonnet", "Sonnet"), ("haiku", "Haiku")]
# (beta) Apple's on-device model, through apple-claude (apple-claude/: a Swift stand-in for claude on FoundationModels,
# its one tool Exec, each command asked for): in the model picker after Claude's; Manual only, no effort
APPLE_MODEL = {"value": "apple", "resolvedModel": "apple-foundation-model", "displayName": "Apple Foundation Model"}


def apple_exe():
    """apple-claude: BAUDE_APPLE_CLAUDE, else as `swift build -c release` in apple-claude/ leaves it; None if not built."""
    exe = os.environ.get("BAUDE_APPLE_CLAUDE") or \
        os.path.join(os.path.dirname(os.path.realpath(__file__)), "apple-claude", ".build", "release", "apple-claude")
    return exe if os.access(exe, os.X_OK) else None


# (beta) models on this Mac, through pi-claude (pi-claude/: pi, the coding agent, on a model mlx_lm.server or
# mlx_vlm.server serves, each tool call asked): those in pi-claude/models.json that are downloaded, after Apple's;
# Manual only, like Apple's, and no effort but a thinking level for those that think; one to another keeps the
# conversation (pi's session), to Claude's doesn't
PI_CLAUDE = os.path.join(os.path.dirname(os.path.realpath(__file__)), "pi-claude")
try:
    with open(os.path.join(PI_CLAUDE, "models.json")) as f:
        LOCAL_MODELS = [{"value": m["value"], "resolvedModel": m["repo"], "displayName": m["displayName"]} |
                        ({"supportsEffort": True, "supportedEffortLevels": ["off", "low", "medium", "high"]}
                         if m.get("thinking") else {}) for m in json.load(f)]
except (OSError, ValueError, KeyError):
    LOCAL_MODELS = []


def local_models():
    """LOCAL_MODELS whose weights are in the Hugging Face cache, while pi (in ~/.local/share/baude/pi) is installed."""
    pi = os.environ.get("BAUDE_PI") or os.path.expanduser("~/.local/share/baude/pi/node_modules/.bin/pi")
    if not os.access(pi, os.X_OK):
        return []
    hub = os.environ.get("HF_HUB_CACHE") or os.path.join(  # (where huggingface_hub looks)
        os.environ.get("HF_HOME") or os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"),
                                                  "huggingface"), "hub")
    return [m for m in LOCAL_MODELS
            if os.path.isdir(os.path.join(hub, "models--" + m["resolvedModel"].replace("/", "--")))]


def model_program(value):
    """What runs a model: "apple" (apple-claude), "pi" (pi-claude), or "claude"."""
    if value == APPLE_MODEL["value"]:
        return "apple"
    return "pi" if any(m["value"] == value for m in LOCAL_MODELS) else "claude"


MODES = [("default", "Manual"), ("acceptEdits", "Accept edits"), ("plan", "Plan"),
         ("auto", "Auto"), ("dontAsk", "Don't ask"), ("bypassPermissions", "Bypass permissions")]


EFFORT_LABELS = {"off": "No thinking", "low": "Low effort", "medium": "Medium effort", "high": "High effort",
                 "xhigh": "Extra-high effort", "max": "Max effort"}


def fmt_tokens(n):
    return f"{n / 1_000_000:.1f}M".replace(".0M", "M") if n >= 1_000_000 else \
        f"{n / 1000:.1f}k".replace(".0k", "k") if n >= 1000 else str(n)


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
        it's what the lists say), as a closed issue's # goes grey and a merged pull request orange. The labels showing
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

    def follow(self, transcript):
        """The chat shown (its transcript): what's new in it is read (what Claude saw, as evidence), and the reader
        reads it, from the start or on from where it was (main loop). Not running, it's only read, so the reader's
        decisions carry from reply to reply as they're drawn. This runs for each line another program appends to the
        chat: only what was appended is read (SignalChat)."""
        if transcript:
            self.transcript = transcript
        if not self.transcript or not os.path.exists(self.transcript):
            return
        new = self._chat is None or self._chat.path != self.transcript
        if new:
            self._chat, self._evidence_from = SignalChat(self.transcript, self.root, self.other_checkouts()), None
        chat = self._chat
        first = chat.read()
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
        while True:
            jobs = [self.jobs.get()]
            if jobs[0][0] == "resolve":  # every reply queued meanwhile, in one go: one git status for all
                while len(jobs) < 200:
                    try:
                        jobs.append(self.jobs.get_nowait())
                    except queue.Empty:
                        break
            batch = [(arg, meta) for job, arg, meta in jobs if job == "resolve"]
            for job, arg, meta in [("batch", batch, None)] * bool(batch) + [j for j in jobs if j[0] != "resolve"]:
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
        st, found, misses = self._repo_state(), [], {}
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
        try:
            with open(path, "rb") as f:
                f.seek(seen["offset"])
                data = f.read()
        except OSError:
            return seen["tops"]
        end = data.rfind(b"\n") + 1  # (whole lines only: the rest is read next time)
        seen["offset"] += end
        home = os.path.expanduser("~")
        mine = {os.path.realpath(t) for t in (self.top, *self.other_checkouts()) if t}
        for line in data[:end].decode("utf-8", "replace").splitlines():
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


MACOS_DESKTOP_CLAUDE = "~/Library/Application Support/Claude/claude-code"


def claude_exe():
    """The claude binary: BAUDE_CLAUDE, else claude on PATH, else (macOS) the newest one the Claude desktop app
    keeps (claude-code/<version>/<build>/claude.app), as the desktop app puts none on PATH."""
    exe = os.environ.get("BAUDE_CLAUDE") or shutil.which("claude")
    if exe or sys.platform != "darwin":
        return exe or "claude"
    found = []
    for path in glob.glob(os.path.join(os.path.expanduser(MACOS_DESKTOP_CLAUDE),
                                       "*", "*", "claude.app", "Contents", "MacOS", "claude")):
        version = os.path.relpath(path, os.path.expanduser(MACOS_DESKTOP_CLAUDE)).split(os.sep)[0]
        if os.access(path, os.X_OK) and re.fullmatch(r"\d+(\.\d+)*", version):
            found.append((tuple(int(n) for n in version.split(".")), os.path.getmtime(path), path))
    return max(found)[2] if found else "claude"


_CLAUDE_VERSION = []


def claude_version():
    """The installed Claude Code's version (2.1.300), or None."""
    if not _CLAUDE_VERSION:
        exe = claude_exe()
        try:
            out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=15,
                                 stdin=subprocess.DEVNULL).stdout
        except (OSError, subprocess.TimeoutExpired):
            out = ""
        m = re.search(r"\b(\d+)\.(\d+)\.(\d+)\b", out)
        _CLAUDE_VERSION.append(m.group(0) if m else None)
    return _CLAUDE_VERSION[0]


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

    def read(self):
        """Read what's new. Returns the number of the first reply whose evidence may have changed (a new reply, or
        one after new evidence: 1 when read from the start, one past the last when only other events changed), or
        None if nothing did."""
        stamp = self._stamp()
        if stamp is None or stamp == self.stamp:
            return None
        self.stamp = stamp
        self._owned, self._first, self._changed = False, None, False
        if self._read() is False:  # (rewritten or cut short: from the start)
            self.restart()
            self._owned, self._first, self._changed = False, 1, True
            if self._read() is False:
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

    def _read(self):
        """On from where it was: False if a file isn't the one read."""
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
        present = {s["tid"]: s["events"] for s in self.subs.values() if s["tid"] and s["events"] is not None}
        new = []
        got = self._lines(self.path, self.offset, self.last,
                          lambda line: new.extend(_ref_events_of(history_items((line,)), present)))
        if got is None:
            return False
        if got is False:
            return None
        self.offset, self.last = got
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
    def _lines(path, offset, last, take):
        """Hands take each whole line of a transcript past offset, decoded, a line at a time (they're mostly tool
        output, up to hundreds of MB), and returns (offset, last) on from there. None if it isn't the file read (it's
        shorter, or its last line read, last: (start, _line_sum), isn't there any more), False if there's no file."""
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


def ref_py_scope(text, line):
    """Where a line of Python is, by Python's own parser (no server needed): what it's directly in ("class",
    "function" or "module"; None if it doesn't parse) and the classes around it, outermost first."""
    import ast
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return None, []
    around = sorted((n for n in ast.walk(tree) if isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                     and n.lineno < line <= (n.end_lineno or n.lineno)), key=lambda n: n.lineno)
    scope = "module" if not around else "class" if isinstance(around[-1], ast.ClassDef) else "function"
    return scope, [n.name for n in around if isinstance(n, ast.ClassDef)]


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


def _diff_block(old, new, path=""):
    """Removed lines on red, added lines on green (no -/+ markers)."""
    lines = [("del", l) for l in (old or "").splitlines()] + [("add", l) for l in (new or "").splitlines()]
    return CodeBlock(new or "", "", diff=lines or [("ctx", "")])


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
        add(CodeBlock(inp["command"], "bash" if base != "PowerShell" else "powershell"))
        rest = _kv_rows(inp, skip=("command", "description"))
    elif base == "Edit" and "file_path" in inp:
        add(_path_label(inp["file_path"], "all occurrences" if inp.get("replace_all") else ""))
        add(_diff_block(inp.get("old_string"), inp.get("new_string")))
        rest = None
    elif base == "MultiEdit" and "file_path" in inp:
        add(_path_label(inp["file_path"], f"{len(inp.get('edits') or [])} edits"))
        for e in inp.get("edits") or []:
            add(_diff_block(e.get("old_string"), e.get("new_string")))
        rest = None
    elif base == "Write" and "file_path" in inp:
        content = inp.get("content") or ""
        lines = content.splitlines()
        add(_path_label(inp["file_path"], f"{len(lines)} lines"))
        shown = "\n".join(lines[:2000]) + (f"\n… {len(lines) - 2000} more lines" if len(lines) > 2000 else "")
        add(CodeBlock(shown, LANG_BY_EXT.get(os.path.splitext(inp["file_path"])[1], "")))
        rest = None
    elif base == "Read" and "file_path" in inp:
        span = ""
        if inp.get("offset") or inp.get("limit"):
            start = inp.get("offset") or 1
            span = f"lines {start}–{start + inp['limit'] - 1}" if inp.get("limit") else f"from line {start}"
        add(_path_label(inp["file_path"], span))
        rest = _kv_rows(inp, skip=("file_path", "offset", "limit"))
    elif base in ("Grep", "Glob") and "pattern" in inp:
        add(CodeBlock(inp["pattern"], "regex" if base == "Grep" else "glob"))
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


ICON_SLOT = 16  # status icon and disclosure arrow share one square column


def _slot(widget):
    widget.set_size_request(ICON_SLOT, ICON_SLOT)  # (at least: an image is at the icons' size, Settings → Size)
    widget.set_halign(Gtk.Align.CENTER)
    widget.set_valign(Gtk.Align.CENTER)
    return widget


class Disclosure:
    """A "details" toggle whose arrow sits in the same icon column as the status above it
    (Gtk.Expander draws its arrow with its own padding, so it can't line up)."""

    def __init__(self, grid, row, label="details"):
        self.on_expand = []  # run on every opening: content built only once someone looks
        self.grid, self.row, self.revealer = grid, row, None  # (the revealer: once there's something in it)
        self.arrow = _slot(Gtk.Image.new_from_icon_name("pan-end-symbolic"))
        self.arrow.add_css_class("soft-icon")
        text = self.text = Gtk.Label(label=label, xalign=0)
        text.add_css_class("dim-label")
        for w in (self.arrow, text):
            click = Gtk.GestureClick()
            click.connect("released", lambda *_: self.set_expanded(not self.get_expanded()))
            w.add_controller(click)
            pointers.hand(w)
        grid.attach(self.arrow, 0, row, 1, 1)
        grid.attach(text, 1, row, 1, 1)
        self.hold_place(True)

    def hold_place(self, on):
        """Until there's a revealer, the arrow and label take the row spacing its (empty, folded) row added below them,
        so a card is as tall either way and nothing moves when it comes."""
        gap = self.grid.get_row_spacing() if on and isinstance(self.grid, Gtk.Grid) else 0
        for w in (self.arrow, self.text):
            w.set_margin_bottom(gap)

    def set_child(self, child):
        if self.revealer is None:  # (a long session has thousands of these, most never opened: made when needed)
            self.revealer = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN,
                                         transition_duration=120, visible=self.arrow.get_visible())
            self.grid.attach(self.revealer, 1, self.row + 1, 1, 1)
            self.hold_place(False)
        self.revealer.set_child(child)

    def set_label(self, label):
        self.text.set_label(label)

    def set_visible(self, visible):
        for w in (self.arrow, self.text, self.revealer):
            if w is not None:
                w.set_visible(visible)

    def get_visible(self):
        return self.arrow.get_visible()

    def get_child(self):
        return self.revealer.get_child() if self.revealer is not None else None

    def get_expanded(self):
        return self.revealer is not None and self.revealer.get_reveal_child()

    def set_expanded(self, expanded):
        if expanded:
            for fn in self.on_expand:
                fn()
        if self.revealer is not None:  # (nothing to show, nothing to reveal: the arrow still turns)
            self.revealer.set_reveal_child(expanded)
        self.arrow.set_from_icon_name("pan-down-symbolic" if expanded else "pan-end-symbolic")


def _status_icon(state):
    """The 16px status mark shared by tool cards and groups."""
    if state in ("running", "background"):
        return _slot(Spinner())  # (not Adw.Spinner, nor Gtk.Spinner: see Ticker)
    if state == "interrupted":  # a saved call that never finished: no spinner forever
        icon = _slot(Gtk.Image.new_from_icon_name("process-stop-symbolic"))
        icon.add_css_class("dim-label")
        return icon
    icon = _slot(Gtk.Image.new_from_icon_name(
        "window-close-symbolic" if state == "error" else "object-select-symbolic"))
    icon.add_css_class("error" if state == "error" else "success")
    return icon


TASK_ENDINGS = {"completed": "finished", "failed": "failed", "killed": "stopped", "error": "failed"}


def task_note_widget(note, card=None, on_jump=None):
    """A background task ending, as one quiet line: ✓ Bash · <its description> — finished (exit 0).
    The description links back to the task's card (on_jump(card)); never the raw command or XML."""
    status = note.get("status") or "completed"
    row = Gtk.Box(spacing=8, halign=Gtk.Align.START)
    row.add_css_class("task-note")
    row.append(_status_icon("error" if status in ("failed", "killed", "error") else "done"))
    summary = html.unescape(note.get("summary") or "")
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
    ending = TASK_ENDINGS.get(status, status)
    if (code := re.search(r"exit code (\d+)", summary)) is not None:
        ending += f" (exit {code.group(1)})"
    text = html.escape(desc)
    if card is not None and on_jump is not None:
        text = f'<a href="card">{text}</a>'
    label = Gtk.Label(xalign=0, wrap=True, selectable=True)
    label.set_markup(f"<b>{html.escape(what)}</b> · {text} — {html.escape(ending)}")
    label.add_css_class("dim-label")
    label.set_tooltip_text(summary or None)
    if card is not None and on_jump is not None:
        label.connect("activate-link", lambda *_: (on_jump(card), True)[1])
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


class ToolCard(Gtk.Box):
    """One tool call. state: running -> done | error, or running -> background -> done | error
    when the CLI runs it as a background task (its result only says it started).
    In a run of calls (ToolGroup), a finished one is a single line, [>] Name  summary: opening it shows what the card's
    rows would (set_line)."""

    def __init__(self, name, tool_id):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.add_css_class("tool-card")
        TOOL_NAMES.add(name)  # (a tool this chat called: Signal can name it)
        self.name, self.tool_id, self.partial_json, self.input = name, tool_id, "", {}
        self.state, self.task, self.decision, self.listeners = "running", None, None, []
        self.artifact = None
        # rows sharing one icon column:  [status] Name  summary
        #                                 [  >   ] command   (what was asked)
        #                                 [  >   ] output    (what came back; once there is some)
        grid = self.grid = Gtk.Grid(column_spacing=8, row_spacing=4)
        self.status = _status_icon("running")
        self.header = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, hexpand=True, selectable=True)
        self.header.set_can_focus(False)  # selectable for copying, but not a tab stop in every card
        grid.attach(self.status, 0, 0, 1, 1)
        grid.attach(self.header, 1, 0, 1, 1)
        self.append(grid)
        base = self.base = name.split("__")[-1]
        self.input_toggle = Disclosure(grid, 1, "command" if base in ("Bash", "BashOutput", "PowerShell")
                                       else "prompt" if base in ("Task", "Agent") else "input")
        self.output_toggle = Disclosure(grid, 3, "output")
        self.has_input, self.has_output = True, False  # (the output's row: once there is some)
        # one line, in a run: (in_run) and when nothing about it needs a look (line); its arrow is made when first needed
        self.in_run, self.line, self.line_open, self.line_arrow = False, False, False, None
        self.show_rows()
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
        if self.status.get_parent() is not None:
            self.grid.remove(self.status)
        self.status = _status_icon(state)
        if state == "error":
            self.add_css_class("error")
            self.output_toggle.set_label("error")
        self.refresh_line()
        self.refresh_header()
        for fn in self.listeners:
            fn(self)

    def set_decision(self, decision):
        """'allowed' / 'denied' / 'always allowed' from a permission prompt, shown in the header."""
        self.decision = decision
        self.refresh_line()
        self.refresh_header()

    # ---- one line, in a run
    def join_run(self):
        """In a run of calls (ToolGroup): from now on a finished call is one line."""
        self.in_run = True
        self.refresh_line()

    def refresh_line(self):
        """One line when it's in a run and nothing about it needs a look: still running (or waiting to be allowed),
        denied, failed or cut short is a card, its status at the front."""
        line = self.in_run and self.state == "done" and self.decision != "denied"
        if line and self.line_arrow is None:
            self.line_arrow = _slot(Gtk.Image.new_from_icon_name("pan-end-symbolic"))
            self.line_arrow.add_css_class("soft-icon")
            # (the arrow is out of the card while it's a card again, where a chat's freeing doesn't reach it: so its
            # click holds the card weakly, or the two would keep each other)
            ref = weakref.ref(self)
            for w in (self.line_arrow, self.header):
                click = Gtk.GestureClick()
                click.connect("released", lambda *_: (c := ref()) is not None and c.line and c.open_line(not c.line_open))
                w.add_controller(click)
            pointers.hand(self.line_arrow)
        self.line = line
        icon, gone = (self.line_arrow, self.status) if line else (self.status, self.line_arrow)
        if gone is not None and gone.get_parent() is not None:
            self.grid.remove(gone)
        if icon.get_parent() is None:
            self.grid.attach(icon, 0, 0, 1, 1)
        (self.add_css_class if line else self.remove_css_class)("tool-line")
        # a click on the line opens it, so it isn't for selecting text then
        if line:
            self.header.set_selectable(False)
            pointers.hand(self.header)
        else:
            pointers.set_pointer(self.header, None)
            self.header.set_selectable(True)
        self.show_rows()

    def open_line(self, opened=True):
        """Open (or close) a line: what the card's rows would show, its command or input and its output, opened."""
        self.line_open = opened
        if self.line_arrow is not None:
            self.line_arrow.set_from_icon_name("pan-down-symbolic" if opened else "pan-end-symbolic")
        for toggle, has in ((self.input_toggle, self.has_input), (self.output_toggle, self.has_output)):
            if has:
                toggle.set_expanded(opened)
        self.show_rows()

    def show_rows(self):
        """The command and output rows: a card's, each with its arrow; a line's, only what they hold, while it's open."""
        for toggle, has in ((self.input_toggle, self.has_input), (self.output_toggle, self.has_output)):
            if not self.line:
                toggle.set_visible(has)
                continue
            toggle.arrow.set_visible(False)
            toggle.text.set_visible(False)
            if has and self.line_open and not toggle.get_expanded():
                toggle.set_expanded(True)  # (output that came after it was opened)
            if toggle.revealer is not None:
                toggle.revealer.set_visible(has and self.line_open)
        if self.line:  # (nothing to open, as an image's Read: no arrow, its place kept)
            self.line_arrow.set_opacity(1 if self.has_input or self.has_output else 0)

    def refresh_header(self):
        s = summarize_tool(self.name, self.input)
        extra = []
        if self.state == "background":
            extra.append("running in background")
        elif self.state == "interrupted":
            extra.append("interrupted")
        if self.decision:
            extra.append(self.decision)
        tail = "".join(f"  <span alpha='55%'>· {html.escape(e)}</span>" for e in extra)
        title, source = tool_display(self.name)
        src = f"  <span alpha='55%'>{html.escape(source)}</span>" if source else ""
        self.header.set_markup(f"<b>{html.escape(title)}</b>{src}  {html.escape(s)}{tail}")

    def show_output(self):
        if not self.has_output:
            self.has_output = True
            self.show_rows()

    def set_input(self, inp):
        self.input = inp if isinstance(inp, dict) else {}
        self.refresh_header()
        # a plain Read's only input is the path, which the header already shows
        redundant = self.name == "Read" and not (self.input.get("offset") or self.input.get("limit") or
                                                 self.input.get("pages"))
        if self.has_input == redundant:
            self.has_input = not redundant
            self.show_rows()
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
                                      margin_top=2, margin_bottom=2)
            self.grid.attach(self._media_box, 1, 5, 1, 1)
        return self._media_box

    def output_boxes(self):
        """(children, result_box): subagent messages land in the first when there's no side-panel view for them."""
        if self._output is None:
            out = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=2, margin_bottom=2)
            self._output = (Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4),
                            Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6))
            out.append(self._output[0])
            out.append(self._output[1])
            self.output_toggle.set_child(out)
            if self.line:
                self.show_rows()  # (a line opened before there was any: shown now)
        return self._output

    children = property(lambda self: self.output_boxes()[0])
    result_box = property(lambda self: self.output_boxes()[1])

    def build_input(self):
        if not self.input_stale:
            return
        self.input_stale = False
        if self.input_box is None:
            self.input_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin_top=2, margin_bottom=2)
            self.input_toggle.set_child(self.input_box)
        if (c := self.input_box.get_first_child()) is not None:
            self.input_box.remove(c)
            discard(c)
        self.input_box.append(tool_input_view(self.name, self.input))

    def build_output(self):
        pending, self.pending_output = self.pending_output, []
        for shown, lang in pending:
            self.result_box.append(CodeBlock(shown, lang))

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
        elif text:
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


class ToolGroup(Gtk.Box):
    """Consecutive tool calls, close together: each finished one a line, [>] Bash  List files, that opens to show
    its command and output; one still running (or waiting to be allowed), denied or failed stays a card
    (ToolCard.refresh_line). What a call showed (images, a published artifact) shows under its line."""

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.add_css_class("tool-group")

    def add_item(self, widget):
        self.append(widget)
        if isinstance(widget, ToolCard):
            widget.join_run()

    def cards(self):
        out, child = [], self.get_first_child()
        while child is not None:
            if isinstance(child, ToolCard):
                out.append(child)
            child = child.get_next_sibling()
        return out


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
    """Popover over the composer listing commands/skills while the first word starts with '/'."""

    MAX_ROWS = 60

    def __init__(self, textview, anchor):
        self.view, self.commands, self.matches = textview, list(LOCAL_COMMANDS), []
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.list.add_css_class("navigation-sidebar")  # visible selection highlight
        self.list.connect("row-activated", lambda _l, row: self.accept(row.get_index()))
        self.scroll = Gtk.ScrolledWindow(max_content_height=320, propagate_natural_height=True,
                                         hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.scroll.set_child(self.list)
        self.pop = Gtk.Popover(child=self.scroll, autohide=False, has_arrow=False, css_classes=["completer"],
                               position=Gtk.PositionType.TOP, can_focus=False)
        self.pop.set_offset(0, -10)  # a clear gap above the input box
        self.pop.set_parent(anchor)
        self.anchor = anchor
        textview.get_buffer().connect("changed", lambda *_: self.refresh())

    def set_commands(self, cmds):
        seen = {n for c in LOCAL_COMMANDS for n in [c["name"], *c.get("aliases", [])]}
        self.commands = list(LOCAL_COMMANDS) + sorted(
            (c for c in cmds if c.get("name") and c["name"] not in seen), key=lambda c: c["name"])
        self.refresh()

    def query(self):
        """The '/word' being typed, or None if we shouldn't complete."""
        buf = self.view.get_buffer()
        text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True)
        m = re.match(r"^/(\S*)$", text)
        cursor = buf.get_iter_at_mark(buf.get_insert()).get_offset()
        return m.group(1).lower() if m and cursor == len(text) else None

    def refresh(self):
        q = self.query()
        if q is None:
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
        scored = [(r, c) for c in self.commands if (r := rank(c)) is not None]
        self.matches = [c for _, c in sorted(scored, key=lambda rc: rc[0])][:self.MAX_ROWS]
        if not self.matches:
            return self.hide()
        while (row := self.list.get_row_at_index(0)) is not None:
            self.list.remove(row)
            discard(row)
        for c in self.matches:
            self.list.append(self.make_row(c))
        self.list.select_row(self.list.get_row_at_index(0))
        self.scroll.get_vadjustment().set_value(0)
        self.pop.set_size_request(self.anchor.get_width(), -1)
        self.pop.popup()

    def make_row(self, c):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin_top=3, margin_bottom=3,
                      margin_start=6, margin_end=6)
        hint = c.get("argumentHint") or ""
        aliases = ", ".join("/" + a for a in c.get("aliases") or [])
        title = f"<b>/{html.escape(c['name'])}</b>"
        if hint:
            title += f"  <span alpha='60%'>{html.escape(hint)}</span>"
        if aliases:
            title += f"  <span alpha='45%' size='small'>({html.escape(aliases)})</span>"
        name = Gtk.Label(xalign=0, use_markup=True, label=title, ellipsize=Pango.EllipsizeMode.END,
                         max_width_chars=1, hexpand=True)
        name.add_css_class("mono")
        desc = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, max_width_chars=1, hexpand=True,
                         label=(c.get("description") or "").split("\n")[0])
        desc.add_css_class("dim-label")
        desc.add_css_class("caption")
        box.append(name)
        box.append(desc)
        return Gtk.ListBoxRow(child=box, tooltip_text=c.get("description") or None)

    def visible(self):
        return self.pop.get_visible()

    def hide(self):
        self.matches = []
        if self.pop.get_visible():
            self.pop.popdown()

    def move(self, step):
        row = self.list.get_selected_row()
        i = max(0, min(len(self.matches) - 1, (row.get_index() if row else -1) + step))
        row = self.list.get_row_at_index(i)
        self.list.select_row(row)
        ok, bounds = row.compute_bounds(self.list)
        if ok:  # keep the selection in view without moving keyboard focus off the textview
            adj, y, h = self.scroll.get_vadjustment(), bounds.get_y(), bounds.get_height()
            if y < adj.get_value():
                adj.set_value(y)
            elif y + h > adj.get_value() + adj.get_page_size():
                adj.set_value(y + h - adj.get_page_size())

    def accept(self, index=None):
        if index is None:
            row = self.list.get_selected_row()
            index = row.get_index() if row else 0
        if not 0 <= index < len(self.matches):
            return
        self.view.get_buffer().set_text(f"/{self.matches[index]['name']} ")  # trailing space hides us
        self.view.grab_focus()

    def handle_key(self, keyval):
        """Returns True if the key was consumed by the popover."""
        if not self.visible():
            return False
        if keyval in (Gdk.KEY_Down, Gdk.KEY_Up):
            self.move(1 if keyval == Gdk.KEY_Down else -1)
        elif keyval in (Gdk.KEY_Tab, Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self.accept()
        elif keyval == Gdk.KEY_Escape:
            self.hide()
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
# Claude Code keeps one JSONL transcript per session under
# $CLAUDE_CONFIG_DIR/projects/<folder with non-alphanumerics as '-'>/<session id>.jsonl.

def projects_dir():
    return os.path.join(os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude"), "projects")


_INJECTED = ("<system-reminder", "<local-command", "Caveat:", "<ide_", "<pasted_content")


_NOT_TYPED = _INJECTED[:3] + ("<task-notification", "<ci-monitor-event")


def _prompt_texts(content):
    """The parts of a user message the person actually wrote (clients prepend reminders, and
    Claude Code sends task notifications as user messages)."""
    texts = [content] if isinstance(content, str) else [
        c.get("text", "") for c in content or [] if isinstance(c, dict) and c.get("type") == "text"]
    return [t for t in texts if t.strip() and not t.lstrip().startswith(_NOT_TYPED)]


def task_notification(text):
    """What a <task-notification> says (Claude Code sends one as a user message when a background
    command or subagent ends): {'status', 'summary'}, or None for any other text."""
    m = re.match(r"\s*<task-notification>(.*?)</task-notification>", text or "", re.S)
    if not m:
        return None
    def tag(name):
        t = re.search(rf"<{name}>(.*?)</{name}>", m.group(1), re.S)
        return t.group(1).strip() if t else ""
    return {"status": tag("status"), "summary": tag("summary") or "A background task finished",
            "task_id": tag("task-id"), "tool_use_id": tag("tool-use-id"), "output_file": tag("output-file"),
            "result": tag("result")}  # a subagent's report


def ci_event(text):
    """What a <ci-monitor-event> says (the desktop app sends one as a user message about a pull request it's watching):
    {'title': its second paragraph's first words, what happened, 'text'}, or None for any other text."""
    m = re.match(r"\s*<ci-monitor-event>(.*?)(?:</ci-monitor-event>|\Z)", text or "", re.S)
    if not m:
        return None
    body = m.group(1).strip()
    said = body.split("\n\n")[1] if "\n\n" in body else ""
    return {"title": re.split(r" at head commit |\. |\n", said, maxsplit=1)[0].strip()[:120] or "A CI event", "text": body}


def clean_prompt(text):
    """Drop paste wrappers that clients put around pasted text."""
    return re.sub(r"</?pasted_content[^>]*>", "", text).strip()


def _first_text(content):
    texts = _prompt_texts(content)
    return texts[0] if texts else ""


def _is_prompt(rec):
    """A user record that is something the person typed (not a tool result or injected text)."""
    if rec.get("type") != "user" or rec.get("isMeta") or rec.get("isSidechain") or rec.get("isCompactSummary"):
        return False
    content = rec.get("message", {}).get("content")
    if isinstance(content, list) and any(isinstance(c, dict) and c.get("type") == "tool_result"
                                         for c in content):
        return False
    has_image = isinstance(content, list) and any(isinstance(c, dict) and c.get("type") == "image"
                                                    for c in content)
    return bool(_first_text(content)) or has_image


def _command_text(text):
    """'<command-name>/foo</command-name><command-args>x</command-args>' -> '/foo x'."""
    name = re.search(r"<command-name>(.*?)</command-name>", text, re.S)
    if not name:
        return text
    args = re.search(r"<command-args>(.*?)</command-args>", text, re.S)
    return f"{name.group(1).strip()} {args.group(1).strip() if args else ''}".strip()


_RECORD_UUID = re.compile(r'"uuid":\s*"([0-9a-f-]{36})"')  # escaped copies inside strings don't match
_RECORD_TIME = re.compile(r'"timestamp":\s*"([0-9T:.\-]+Z?)"')
_KEY_BITS = (1 << 60) - 1


def record_key(uuid):
    """A record's uuid as the session index keeps it: its two halves folded into a 60-bit int, 32 bytes to the
    string's 80 (there are hundreds of thousands). Two of a machine's ids folding the same is a
    one-in-a-hundred-million chance, and would count one record as shared that isn't."""
    n = int(uuid.replace("-", ""), 16)
    return (n ^ n >> 64) & _KEY_BITS


def _pack_keys(keys):
    """Record keys as text for the saved index (8 bytes each, little-endian, base64)."""
    packed = array.array("q", keys)
    if sys.byteorder == "big":
        packed.byteswap()
    return base64.b64encode(packed.tobytes()).decode("ascii")


def _unpack_keys(text, seen):
    """_pack_keys undone. seen: the keys unpacked so far, so a record in several sessions (copies, forks) is one int."""
    packed = array.array("q", base64.b64decode(text))
    if sys.byteorder == "big":
        packed.byteswap()
    return frozenset({seen.setdefault(k, k) for k in packed})  # (made from a set: a smaller table than from the array)


def read_session_info(path):
    """Title, folder and timestamps for one transcript, reading as little JSON as possible."""
    return scan_session(path)[1]


_CHAT_MODEL = re.compile(rb'"isSidechain":\s*false,\s*"message":\s*\{\s*"model":\s*"([^"<][^"]*)"')


def transcript_model(path):
    """The model a transcript's last reply came from (a subagent's, or a <synthetic> one, aside): its last 256 KB
    first, all of it if they hold none. None if it has none."""
    try:
        with open(path, "rb") as f:
            size = f.seek(0, 2)
            for back in (1 << 18, size):
                f.seek(max(0, size - back))
                if hits := _CHAT_MODEL.findall(f.read()):
                    return hits[-1].decode()
                if back >= size:
                    break
    except OSError:
        pass
    return None


_TOOL_RESULT_REC = re.compile(r'"content":\s*\[\s*\{\s*"(?:tool_use_id|type":\s*"tool_result)')


def scan_session(path, state=None):
    """(state, info) for a transcript. state is what's been read so far (byte offset and running
    tallies); pass it back and only what was appended since is read: a session being written to
    is re-scanned on every change, and they grow to tens of MB. info is None if it isn't a
    session worth listing (no prompts and no title). Its record ids (record_key) are the state's own
    frozensets, shared with the info: carrying on copies them first, so the state passed in isn't changed."""
    if state:
        st = {**state, "uuids": set(state["uuids"]), "foreign": set(state["foreign"])}
    else:
        st = {"offset": 0, "title": None, "agent_name": None, "last_prompt": None, "relocated": None,
              "summary": None, "uuids": set(), "born": None, "foreign": set(), "cwd": None, "source": "",
              "prompts": 0, "first_prompt": None, "cost": 0.0, "said": None}
    sid = os.path.splitext(os.path.basename(path))[0]
    try:
        mtime = os.path.getmtime(path)
        with open(path, "rb") as f:
            f.seek(st["offset"])
            for raw in f:  # (a line at a time: read whole, a 177 MB transcript took twice that at once)
                if not raw.endswith(b"\n"):
                    break  # a line still being written is read next time
                st["offset"] += len(raw)
                _scan_line(st, str(memoryview(raw)[:-1], "utf-8", "replace"), sid)
    except OSError:
        return state, None
    st["uuids"], st["foreign"] = frozenset(st["uuids"]), frozenset(st["foreign"])
    info = {"id": sid, "path": path, "title": st["title"], "source": st["source"],
            "cwd": st["relocated"] or st["cwd"], "first_prompt": st["first_prompt"], "prompts": st["prompts"],
            "mtime": mtime, "uuids": st["uuids"], "born": st["born"], "copied_from": st["foreign"],
            "cost": st.get("cost") or 0.0,
            # (when it was last used: the last thing typed in it, else when it began; not the file's time, which moves
            # with every reply and every record claude keeps)
            "said": ref_epoch(st.get("said")) or ref_epoch(st["born"]) or mtime}
    first = " ".join(clean_prompt(st["first_prompt"] or st["last_prompt"] or "").split())
    first = re.sub(r"(\*\*|__|`|~~|^#+\s*)", "", first)  # titles are plain text
    info["title"] = info["title"] or st["agent_name"] or st["summary"] or \
        (first[:90] + ("…" if len(first) > 90 else "")) or None
    return st, (info if info["prompts"] or info["title"] else None)


def _scan_line(st, line, sid):
    """What one transcript line adds to scan_session's tallies."""
    if (m := _RECORD_UUID.search(line)) is not None:
        st["uuids"].add(record_key(m.group(1)))
    if st["born"] is None and (m := _RECORD_TIME.search(line)) is not None:
        st["born"] = m.group(1)
    if len(line) < 4096 and ('"custom-title"' in line or '"agent-name"' in line or '"relocated"' in line
                             or '"last-prompt"' in line or line.startswith('{"type":"summary"')
                             or line.startswith('{"type":"cost-state"')):
        try:  # (these records are tiny: no need to search the big ones for them)
            rec = json.loads(line)
        except ValueError:
            return
        t = rec.get("type")
        if t == "custom-title":
            st["title"] = rec.get("customTitle") or st["title"]
        elif t == "agent-name":
            st["agent_name"] = rec.get("agentName") or st["agent_name"]
        elif t == "relocated":
            st["relocated"] = rec.get("relocatedCwd") or st["relocated"]
        elif t == "last-prompt":
            st["last_prompt"] = rec.get("lastPrompt") or st["last_prompt"]
        elif t == "summary":  # titles written by the terminal claude
            st["summary"] = rec.get("summary") or st["summary"]
        elif t == "cost-state":  # (what claude had spent on it when it last exited: --resume counts on from this)
            st["cost"] = float(rec.get("totalCostUSD") or 0)
        if t in ("custom-title", "agent-name", "relocated", "last-prompt", "summary", "cost-state"):
            return
    head = line[:2000]
    if '"queued_command"' in line[:300]:  # (typed while a turn ran: kept as an attachment, not a user record)
        try:
            when = widgets.typed_at(json.loads(line))
        except ValueError:
            when = None
        if when and when > (st.get("said") or ""):
            st["said"] = when
        return
    if '"type":"user"' not in head and '"type": "user"' not in head:
        return
    if st["cwd"] is not None and _TOOL_RESULT_REC.search(line[:1500]):
        return  # a tool's result, not something typed: no need to decode it
    try:
        rec = json.loads(line)
    except ValueError:
        return
    if rec.get("type") != "user":
        return
    if rec.get("sessionId") and rec["sessionId"] != sid:
        st["foreign"].add(rec["sessionId"])
    if st["cwd"] is None:
        st["cwd"] = rec.get("cwd")
        st["source"] = SESSION_SOURCES.get(rec.get("entrypoint"), rec.get("entrypoint") or "")
    if (when := widgets.typed_at(rec)) and when > (st.get("said") or ""):
        st["said"] = when
    if _is_prompt(rec):
        st["prompts"] += 1
        if st["first_prompt"] is None:
            st["first_prompt"] = _command_text(_first_text(rec["message"]["content"]))


SESSION_SOURCES = {"cli": "Claude Code (terminal)", "claude-desktop": "Claude Desktop",
                   "sdk-cli": "Baude / SDK", "sdk-ts": "Agent SDK", "sdk-py": "Agent SDK"}


def collapse_copies(sessions, keep=()):
    """Drop stale copies of a conversation. When Claude Desktop continues a session it starts a
    new one and copies the whole history into it, leaving the old file behind: the sidebar showed
    "Baude GTK harness prototype" four times. An older session counts as a copy when a newer one
    in the same folder has the same title and holds at least 95% of its records (the last few
    differ) — or the newer one is itself 95% made of the older's records (Desktop sometimes copies
    only the history since the last compaction). Forks keep their own title ("... (fork)"), so they stay. Sessions in `keep` (the
    open one) are never dropped. Survivors get "copies": how many were folded into them."""
    kept, newest_first = [], sorted(sessions, key=lambda i: i["mtime"], reverse=True)
    for info in newest_first:
        mine = record_ids(info)
        owner = None
        if info["id"] not in keep and sum(map(len, mine)) >= 5:
            for newer in kept:
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
    """The ids of the sessions a Claude Desktop chat came from, nearest first: the one it was forked from, else the
    one that started it as a side session (Desktop's spawnedFrom)."""
    return [i for i in (info.get("fork_of"), info.get("spawned_from")) if i]


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
    """Claude Desktop's own list of its chats ({cli session id: entry}), from
    ~/.config/Claude/claude-code-sessions/*/*/local_*.json: title, archived, which earlier
    transcripts are copies of it (priorCliSessionIds) and what it was forked from. Empty without
    Desktop. Entries are cached by file mtime (they're small, but carry big tool lists)."""
    base = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
                        "Claude", "claude-code-sessions")
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
_DESKTOP_KEYS = ("sessionId", "cliSessionId", "title", "isArchived", "priorCliSessionIds", "forkedFromSessionId",
                 "spawnedFrom", "cwd", "archived")


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
    archived state, fork parent and the chat that started them as a side session (spawned_from). Sessions from
    elsewhere (terminal, baude) are left alone."""
    if not index:
        return sessions
    prior = {p: cli for cli, e in index.items() for p in e.get("priorCliSessionIds") or ()}
    local = {e.get("sessionId"): cli for cli, e in index.items()}
    out, owners = [], {}
    for info in sessions:
        e = index.get(info["id"])
        if e is not None:
            spawned = e.get("spawnedFrom") if isinstance(e.get("spawnedFrom"), dict) else {}
            info = {**info, "desktop": True, "title": e.get("title") or info.get("title"),
                    "desktop_archived": e["_archived"], "fork_of": local.get(e.get("forkedFromSessionId")),
                    "spawned_from": local.get(spawned.get("sessionId"))}
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
    yet now; what the sidebar's order and its days go by."""
    return info.get("said") or info.get("mtime") or 0.0


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


SCRATCH_MARK = os.sep + os.path.join(".config", "Claude", "scratch-workspaces") + os.sep


def session_group(cwd):
    """(group key, label) for a session's folder; Claude Desktop's per-chat scratch folders
    are one group, like its "No folder" chats."""
    if not cwd:
        return "?", "Unknown folder"
    if SCRATCH_MARK in cwd:
        return "scratch", "No folder (Claude Desktop)"
    return cwd, pretty_path(cwd)


def short_group_label(key, label):
    """A group's name as a header (or a row, by date) shows it: a folder's last part, the rest as they are."""
    if key in ("scratch", "?", "archived") or key.startswith("date:"):
        return label
    return os.path.basename(label.rstrip("/")) or label


def date_group(mtime, now):
    """(key, label) of a chat's day by its last activity, in local time: "Today", "Yesterday", each of the 3 days
    before ("Oct 20"), else "Older". Keys say how many days ago (what's collapsed stays as the days move on)."""
    import datetime
    day = datetime.date.fromtimestamp(mtime)
    ago = (datetime.date.fromtimestamp(now) - day).days
    if ago <= 0:  # (a clock that's behind: today)
        return "date:0", "Today"
    if ago == 1:
        return "date:1", "Yesterday"
    return (f"date:{ago}", f"{day:%b} {day.day}") if ago <= 4 else ("date:older", "Older")


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
    if not path or SCRATCH_MARK in path:
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


def session_file(session_id):
    """A session's transcript, whichever project it's in: None if there's none."""
    for path in [os.path.join(projects_dir(), d, f"{session_id}.jsonl")
                 for d in (os.listdir(projects_dir()) if os.path.isdir(projects_dir()) else [])]:
        if os.path.exists(path):
            return path
    return None


def find_session(session_id):
    return read_session_info(path) if (path := session_file(session_id)) else None


def history_items(lines, sidechain=False):
    """Visible items from transcript lines: ('user'|'assistant', content, is_error),
    ('tool_results', content) or ('artifact', info). Sidechain records are skipped unless
    sidechain (a subagent's own transcript is nothing but); meta records always are."""
    return [item for line in lines if (item := history_item(line, sidechain)) is not None]


def history_item(line, sidechain=False):
    """The visible item one transcript line makes (see history_items), or None: a line makes one at most."""
    if '"frame-link"' in line:  # Claude Desktop's record of an artifact it published
        try:
            rec = json.loads(line)
        except ValueError:
            return None
        if rec.get("frameUrl"):
            return "artifact", {"url": rec["frameUrl"], "title": rec.get("title"), "path": rec.get("path")}
        return None
    if '"type":"user"' not in line and '"type":"assistant"' not in line \
            and '"type": "user"' not in line and '"type": "assistant"' not in line:
        return None
    try:
        rec = json.loads(line)
    except ValueError:
        return None
    if rec.get("type") not in ("user", "assistant") or rec.get("isMeta") or \
            (rec.get("isSidechain") and not sidechain):
        return None
    content = rec.get("message", {}).get("content")
    if rec.get("isCompactSummary"):
        return "compact_summary", _first_text(content) or ""
    if rec["type"] == "user" and isinstance(content, str) and (note := task_notification(content)):
        return "task_note", note
    if rec["type"] == "user" and (event := ci_event(content if isinstance(content, str) else next(
            (c.get("text") for c in content or () if isinstance(c, dict) and c.get("type") == "text"), ""))):
        return "ci_event", event
    if rec["type"] == "user" and not _is_prompt(rec):
        if isinstance(content, list) and any(isinstance(c, dict) and c.get("type") == "tool_result"
                                             for c in content):
            return "tool_results", content
        return None
    if rec["type"] == "user" and isinstance(content, str):
        content = _command_text(content)
    return rec["type"], content, rec.get("isApiErrorMessage", False), rec.get("timestamp")


def _has_text(content):
    """Whether an assistant message has words in it, its thinking's too (most are tool calls only: one record per
    call)."""
    if isinstance(content, str):
        return bool(content.strip())
    return any(isinstance(c, dict) and c.get("type") in ("text", "thinking") and
               (c.get("text") or c.get("thinking") or "").strip() for c in content or ())


def _line_sum(raw):
    """What a transcript line (bytes, with or without its newline) hashes to: whether it's still the line indexed."""
    return hash(memoryview(raw)[:-1] if raw.endswith(b"\n") else raw)


class HistoryIndex:
    """The items of an open chat's history not drawn yet, kept as where their lines are in the transcript rather
    than parsed: each earlier chunk is read and parsed again as it's drawn (kept parsed, a long session's tool
    outputs and images came to ~90 MB of objects, for a chat that draws its newest 60 items at first).
    Transcripts are only appended to; one rewritten or cut short since (a line no longer the one indexed) is
    indexed again, and what's before the oldest item drawn is found in it as it is now.
    Opening reads the newest items from the end; the start of a long transcript is indexed after, a little at a
    time (index_some, from the window's idle time): read whole first, a 177 MB one held the window a second."""

    AT_ONCE = 2 << 20  # (up to this much before the newest items is just indexed on opening)

    def __init__(self, path):
        self.path = path
        self.starts = array.array("q")  # where each item not drawn yet starts in the file,
        self.sums = array.array("q")    # its line's _line_sum,
        self.replies = bytearray()      # and 1 if it's a reply of Claude's (what Signal reads)
        self.oldest = None              # the _line_sum of the oldest item drawn
        self.shown = 0                  # items drawn (those tailed in since too)
        self.todo = None                # [from, to]: the start of the file, not indexed yet (index_some)
        self.later = None               # (the items indexed on opening, after that part: they go after it)

    @property
    def unshown(self):
        """How many items aren't drawn yet (once indexed: see more)."""
        self.finish()
        return len(self.starts)

    @property
    def more(self):
        """Whether anything's left to draw: some items, or a part not indexed yet."""
        return bool(self.todo) or bool(self.starts)

    def count(self):
        """How many items aren't drawn yet; None while the start is still being indexed."""
        return None if self.todo else len(self.starts)

    def load(self, keep):
        """The newest `keep` items, parsed (the ones drawn on opening), read from the end of the transcript; what's
        before them is indexed now if it's short, else by index_some."""
        newest = self._tail(keep)
        self.shown = len(newest)
        if self.todo and self.todo[1] <= self.AT_ONCE:
            self.finish()
        return newest

    def _tail(self, keep):
        """Read the end of the file back far enough to hold `keep` items (twice as far each time it doesn't); the
        items there are indexed, the newest `keep` returned, and what's before is left to index_some."""
        import collections
        try:
            size = os.path.getsize(self.path)
        except OSError:
            return []
        span = 1 << 20
        while True:
            begin = max(0, size - span)
            starts, sums, replies = array.array("q"), array.array("q"), bytearray()
            newest = collections.deque(maxlen=keep)
            try:
                with open(self.path, "rb") as f:
                    f.seek(begin)
                    at = first = begin + (len(f.readline()) if begin else 0)  # (a line cut in two: the part before's)
                    for raw in f:
                        if (item := history_item(raw.decode("utf-8", "replace"))) is not None:
                            starts.append(at)
                            sums.append(_line_sum(raw))
                            replies.append(item[0] == "assistant" and not item[2] and _has_text(item[1]))
                            newest.append(item)
                        at += len(raw)
            except OSError:
                return []
            if len(newest) == keep or not begin:
                break
            span *= 4
        undrawn = len(starts) - len(newest)
        self.oldest = sums[undrawn] if newest else None
        del starts[undrawn:], sums[undrawn:], replies[undrawn:]
        self.starts, self.sums, self.replies = array.array("q"), array.array("q"), bytearray()
        self.later = (starts, sums, replies)
        self.todo = [0, first] if begin else None
        if not self.todo:
            self._join()
        return list(newest)

    def _join(self):
        if self.later is not None:
            starts, sums, replies = self.later
            self.starts.extend(starts)
            self.sums.extend(sums)
            self.replies.extend(replies)
            self.later = None
        self.todo = None

    def index_some(self, seconds=0.01):
        """Index the start of the transcript for about `seconds`; True while there's more."""
        if not self.todo:
            return False
        end_at = time.monotonic() + seconds
        try:
            with open(self.path, "rb") as f:
                f.seek(self.todo[0])
                at = self.todo[0]
                while at < self.todo[1]:
                    raw = f.readline()
                    if not raw:  # (cut shorter since: what's indexed is all there is; drawing finds where it went)
                        at = self.todo[1]
                        break
                    if (item := history_item(raw.decode("utf-8", "replace"))) is not None:
                        self.starts.append(at)
                        self.sums.append(_line_sum(raw))
                        self.replies.append(item[0] == "assistant" and not item[2] and _has_text(item[1]))
                    at += len(raw)
                    if time.monotonic() > end_at:
                        break
                self.todo[0] = at
        except OSError:
            self.todo[0] = self.todo[1]
        if self.todo[0] >= self.todo[1]:
            self._join()
            return False
        return True

    def finish(self):
        """Index what's left of the start now (drawing an earlier chunk needs all of it)."""
        while self.index_some(1.0):
            pass

    def _index(self, keep):
        """(Re)read the whole file; all but the newest `keep` items are left undrawn, and those are returned."""
        import collections
        starts, sums, replies, newest = array.array("q"), array.array("q"), bytearray(), collections.deque(maxlen=keep)
        try:
            with open(self.path, "rb") as f:
                at = 0
                for raw in f:  # (one line at a time: only the newest items stay parsed)
                    if (item := history_item(raw.decode("utf-8", "replace"))) is not None:
                        starts.append(at)
                        sums.append(_line_sum(raw))
                        replies.append(item[0] == "assistant" and not item[2] and _has_text(item[1]))
                        newest.append(item)
                    at += len(raw)
        except OSError:
            pass
        undrawn = len(starts) - len(newest)
        self.oldest = sums[undrawn] if newest else None
        del starts[undrawn:], sums[undrawn:], replies[undrawn:]
        self.starts, self.sums, self.replies, self.todo, self.later = starts, sums, replies, None, None
        return list(newest)

    def _reindex(self):
        """The file isn't what was indexed: index it again, and take as undrawn what's before the oldest item drawn
        (by its line's hash, the last such line); not there, nothing is."""
        oldest = self.oldest
        self._index(0)
        sums = self.sums
        at = next((i for i in range(len(sums) - 1, -1, -1) if sums[i] == oldest), 0)
        del self.starts[at:], self.sums[at:], self.replies[at:]
        self.oldest = oldest

    def _read(self, begin, end):
        """Undrawn items begin..end, parsed again; None if a line isn't the one indexed."""
        items = []
        try:
            with open(self.path, "rb") as f:
                for i in range(begin, end):
                    f.seek(self.starts[i])
                    raw = f.readline()
                    if _line_sum(raw) != self.sums[i] or \
                            (item := history_item(raw.decode("utf-8", "replace"))) is None:
                        return None
                    items.append(item)
        except OSError:
            return None
        return items

    def earlier(self, n):
        """The n items before those drawn (fewer at the start), parsed and in order: they count as drawn now."""
        self.finish()
        begin = max(0, self.unshown - n)
        items = self._read(begin, self.unshown)
        if items is None:
            self._reindex()
            begin = max(0, self.unshown - n)
            items = self._read(begin, self.unshown)
        if items is None:  # (still not the file indexed, or gone: nothing earlier to draw)
            begin, items = 0, []
        else:
            self.shown += len(items)
            self.oldest = self.sums[begin] if items else self.oldest
        del self.starts[begin:], self.sums[begin:], self.replies[begin:]
        return items

    def earlier_replies(self):
        """Claude's replies among the items not drawn yet, newest first, each parsed as it's needed."""
        self.finish()
        try:
            with open(self.path, "rb") as f:
                for i in range(self.unshown - 1, -1, -1):
                    if self.replies[i]:
                        f.seek(self.starts[i])
                        raw = f.readline()
                        if _line_sum(raw) != self.sums[i]:
                            return  # (rewritten: drawing finds where they went)
                        yield history_item(raw.decode("utf-8", "replace"))
        except OSError:
            return


def read_history(path, limit=400):
    """The visible conversation of a transcript (see history_items). Returns (items, skipped)."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            items = history_items(f)
    except OSError:
        return [], 0
    skipped = max(0, len(items) - limit) if limit else 0
    return items[skipped:], skipped


# ------------------------------------------------------------- the process --

# what a claude started from inside another one inherits (baude run from a Claude Desktop or terminal
# session): it would label its transcript as that host's ("Claude Desktop"), claim its session id and
# talk to its messaging socket. baude's claude is its own session.
def claude_env():
    return {k: v for k, v in os.environ.items() if k not in HOST_SESSION_ENV}


def spent_since(proc, msg):
    """What a result adds to what's been spent. Its total_cost_usd is the session's so far, not the turn's: every turn's
    again, added up, came to six times what was spent in seven turns. On --resume claude counts on from the session's
    last cost-state (written as it exits; not if it's killed), so proc.cost_seen starts there (h_result) and is the
    last total it gave; one lower than that is a count started again."""
    spent = float(msg.get("total_cost_usd") or 0)
    seen = getattr(proc, "cost_seen", 0.0)
    if proc is not None:
        proc.cost_seen = spent
    return spent - seen if spent >= seen else spent


class ClaudeProc:
    """Owns the claude subprocess. Callbacks are delivered on the GTK main loop."""

    def __init__(self, on_msg, on_exit, cwd, model, mode, resume=None, effort=None, extra=(), env=None, exe=None):
        self.on_msg, self.on_exit = on_msg, on_exit
        self.req_ids = itertools.count(1)
        exe = exe or claude_exe()  # (apple-claude for Apple's model: start_proc)
        args = [exe, "-p", "--input-format", "stream-json", "--output-format", "stream-json",
                "--verbose", "--include-partial-messages", "--permission-prompt-tool", "stdio",
                "--forward-subagent-text"]  # subagents' own messages, for the side panel
        if mode:  # the flag spells the protocol's "default" mode as "manual"
            args += ["--permission-mode", "manual" if mode == "default" else mode]
        if model:
            args += ["--model", model]
        if resume:
            args += ["--resume", resume]
        if effort:
            args += ["--effort", effort]
        args += list(extra)
        self.p = subprocess.Popen(args, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True, bufsize=1, env=claude_env() | (env or {}))
        threading.Thread(target=self._read, args=(self.p.stdout, False), daemon=True).start()
        threading.Thread(target=self._read, args=(self.p.stderr, True), daemon=True).start()
        self.init_rid = self.control({"subtype": "initialize", "hooks": None})

    def _read(self, stream, is_err):
        for line in stream:
            line = line.strip()
            if not line:
                continue
            if is_err:
                GLib.idle_add(self.on_msg, self, {"type": "_stderr", "text": line})
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                msg = {"type": "_stderr", "text": line}
            GLib.idle_add(self.on_msg, self, msg)
        if not is_err:
            code = self.p.wait()
            self.close_stdin()
            GLib.idle_add(self.on_exit, code)

    def send(self, obj):
        try:
            self.p.stdin.write(json.dumps(obj) + "\n")
            self.p.stdin.flush()
        except (BrokenPipeError, ValueError):
            self.close_stdin()

    def close_stdin(self):
        """Close our end of the pipe now: left to the garbage collector, unsent data would be
        flushed into a dead process and print a BrokenPipeError traceback."""
        try:
            self.p.stdin.close()
        except (BrokenPipeError, OSError, ValueError):
            pass  # close() still closes the file when its final flush fails

    def user(self, content):
        """content: a string, or a list of content blocks (text / image / document)."""
        self.send({"type": "user", "message": {"role": "user", "content": content},
                   "parent_tool_use_id": None, "session_id": ""})

    def control(self, request):
        rid = f"baude_{next(self.req_ids)}"
        self.send({"type": "control_request", "request_id": rid, "request": request})
        return rid

    def respond(self, request_id, response):
        self.send({"type": "control_response",
                   "response": {"subtype": "success", "request_id": request_id, "response": response}})

    def kill(self):
        if self.p.poll() is None:
            self.p.terminate()
        self.close_stdin()


# ------------------------------------------------------- sessions dialog --

class Ticker:
    """What moves while something works: a working session's dot pulsing (its opacity eased between 1 and 0.25 and
    back every 0.9 s) and the spinners of calls still running (Spinner). One timer moves them all, FPS times a
    second: each frame GTK draws costs 1.5-3 ms of CPU on a Mac (most of it GSK readying the window's surface), and
    a CSS animation or an Adw.Spinner draws 60 a second, so one working session's dot, or one long-running command,
    took a sixth of a core. In a window you aren't looking at they hold still."""

    FPS, IDLE_MS, PERIOD, LOW, STILL = 20, 500, 0.9, 0.25, 0.6
    # (held, not weakly: a widget Python keeps no wrapper of, Signal's button's icon, lost its own at once; let go of
    # as each is unmapped, which taking it out of the window does, and taken back as it's mapped again)
    pulsing, spinning, source, interval, start = set(), set(), None, 0, 0.0

    @classmethod
    def run(cls, ms=None):
        """The timer: FPS times a second while something it moves is seen, else every IDLE_MS (to notice when it is)."""
        ms = ms or 1000 // cls.FPS
        if cls.source is not None:
            if cls.interval == ms:
                return
            GLib.source_remove(cls.source)
        else:
            cls.start = time.monotonic()
        cls.interval, cls.source = ms, GLib.timeout_add(ms, cls.tick)

    @classmethod
    def pulse(cls, widget):
        cls.pulsing.add(widget)
        cls.run()

    @classmethod
    def still(cls, widget):
        cls.pulsing.discard(widget)
        widget.set_opacity(1.0)

    @staticmethod
    def seen(widget):
        """Mapped, in the window you're looking at."""
        root = widget.get_root() if widget.get_mapped() else None
        return isinstance(root, Gtk.Window) and root.is_active()

    @classmethod
    def tick(cls):
        for held in (cls.pulsing, cls.spinning):
            held.difference_update([w for w in held if not w.get_mapped()])
        if not cls.pulsing and not cls.spinning:
            cls.source = None
            return False
        phase = ((time.monotonic() - cls.start) / cls.PERIOD) % 2
        ease = (1 - math.cos(math.pi * (phase if phase < 1 else 2 - phase))) / 2  # (CSS's ease-in-out, near enough)
        level, seen = round(1 - (1 - cls.LOW) * ease, 2), False
        for w in cls.pulsing:
            here = cls.seen(w)
            seen = seen or here
            w.set_opacity(level if here else cls.STILL)
        for w in cls.spinning:
            if cls.seen(w):
                seen = True
                w.queue_draw()
        if (ms := 1000 // cls.FPS if seen else cls.IDLE_MS) != cls.interval:  # (the pulse's phase goes on)
            cls.interval, cls.source = ms, GLib.timeout_add(ms, cls.tick)
            return False
        return True


class Spinner(Gtk.Widget):
    """Adw.Spinner's look (a faint ring, and an arc going round it that grows and shrinks), moved by Ticker at its
    frame rate rather than by a frame-clock animation of its own (see Ticker)."""

    def __init__(self, **kw):
        super().__init__(**kw)
        # (plain functions, given the spinner by the signal: a closure over it would be a cycle PyGObject can't free)
        self.connect("map", Spinner._mapped)
        self.connect("unmap", Spinner._unmapped)

    def _mapped(self):
        Ticker.spinning.add(self)
        Ticker.run()

    def _unmapped(self):
        Ticker.spinning.discard(self)

    def do_measure(self, orientation, for_size):
        return _scales["icon_px"], _scales["icon_px"], -1, -1  # (an icon's size: Settings → Size)

    def do_snapshot(self, snapshot):
        w, h = self.get_width(), self.get_height()
        width = max(1.5, min(w, h) / 8)
        r = min(w, h) / 2 - width / 2
        if r <= 0:
            return
        cx, cy, color = w / 2, h / 2, self.get_color()
        track = color.copy()
        track.alpha *= 0.15
        stroke = Gsk.Stroke.new(width)
        ring = Gsk.PathBuilder.new()
        ring.add_circle(Graphene.Point().init(cx, cy), r)
        snapshot.append_stroke(ring.to_path(), stroke, track)
        t = time.monotonic()
        a = (t % 1.0) * 2 * math.pi  # (a turn a second, the arc a fifth to two thirds of the ring, and back)
        span = (0.2 + 0.47 * (1 - math.cos(2 * math.pi * t / 1.6)) / 2) * 2 * math.pi
        arc = Gsk.PathBuilder.new()
        arc.move_to(cx + r * math.cos(a), cy + r * math.sin(a))
        arc.svg_arc_to(r, r, 0, span > math.pi, True, cx + r * math.cos(a + span), cy + r * math.sin(a + span))
        stroke.set_line_cap(Gsk.LineCap.ROUND)
        snapshot.append_stroke(arc.to_path(), stroke, color)


class ContextMeter(Gtk.LevelBar):
    """The status bar's context bar: WIDTH wide given the room, narrower as the chat is (a width_request held it open)."""

    WIDTH = 90  # px

    def __init__(self, **kw):
        super().__init__(**kw)
        self.set_layout_manager(widgets.WidthLayout())

    def width_of(self, least, natural):
        return least, max(natural, self.WIDTH)


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
        self.new_btn = self._action("document-new-symbolic", "New session", "Ctrl+N",
                                    lambda: win.switch_to(win.new_session))
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
        self.view_btn = Gtk.MenuButton(icon_name="view-more-symbolic", menu_model=view, tooltip_text="Group by",
                                       valign=Gtk.Align.CENTER)
        self.view_btn.add_css_class("flat")
        find_row.append(self.view_btn)
        top.append(find_row)
        self.append(top)
        self.list = TreeList(selection_mode=Gtk.SelectionMode.SINGLE)
        self.list.add_css_class("navigation-sidebar")
        self.list.connect("row-activated", self.on_activate)
        self.scroller = Gtk.ScrolledWindow(child=self.list, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.append(self.scroller)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self.on_key)
        self.list.add_controller(keys)
        actions = Gio.SimpleActionGroup()
        for name, fn in (("rename", self.ask_rename), ("archive", self.archive), ("unarchive", self.unarchive),
                         ("trash", self.ask_trash)):
            act = Gio.SimpleAction.new(name, GLib.VariantType.new("s"))
            act.connect("activate", lambda _a, v, fn=fn: fn(self.find_info(v.get_string())))
            actions.add_action(act)
        group = Gio.SimpleAction.new_stateful("group", GLib.VariantType.new("s"),
                                              GLib.Variant.new_string("date" if self.by_date() else "folder"))
        group.connect("change-state", self.on_group_changed)
        actions.add_action(group)
        self.insert_action_group("sb", actions)
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
        place_sessions; a Desktop chat is in the group of the one it came from); or by day (date_group), newest first,
        where only the top-level sessions go by their day: a fork stays under the session it came from, in that one's
        day, whatever its own."""
        q = self.search.get_text().strip().lower()
        here = session_group(self.win.cwd)[0]
        by_date, now = self.by_date(), self.clock()
        groups, listed = {}, []
        archived = self.archived_ids()
        for info in sorted(self.sessions, key=last_used, reverse=True):  # (the last typed in first)
            if info["id"] in archived:
                continue
            folder = info.get("folder", info.get("cwd"))
            key, label = session_group(folder)
            tree = os.path.basename(info.get("worktree") or "")
            if q and q not in f"{info.get('title') or ''} {label} {tree}".lower():
                continue
            listed.append((info, key, label, folder))
        # a chat Desktop started from another (a side session, a fork) is in that one's group, under it, whatever its
        # own folder (with its folder on its row)
        by_id = {info["id"]: (info, key, label, folder) for info, key, label, folder in listed}
        for info, key, label, folder in listed:
            top, seen = info["id"], set()
            while top not in seen and (up := next((i for i in desktop_parents(by_id[top][0]) if i in by_id), None)):
                seen.add(top)
                top = up
            _info, key, label, folder = by_id[info["id"] if top in seen else top]  # (a loop: where it is)
            groups.setdefault(key, [label, folder if key != "scratch" else None, []])[2].append(info)
        # the open session may not be saved yet: shown once it's something (typed into, sent), never while it's blank
        sid = self.win.session_id
        if not q and self.win.chat_begun() and not any(i["id"] == sid for g in groups.values() for i in g[2]):
            key, label = session_group(self.win.cwd)
            groups.setdefault(key, [label, self.win.cwd, []])[2].insert(0, {
                "id": sid, "title": getattr(self.win, "session_title", None) or "New session",
                "cwd": self.win.run_cwd, "folder": self.win.cwd, "worktree": main_folder(self.win.run_cwd)[1],
                "mtime": now, "prompts": 0, "unsaved": True})
        # (forks are found within a folder, by day too: a fork is in its session's folder)
        groups = {k: [label, folder, fork_tree(items)] for k, (label, folder, items) in groups.items()}
        if by_date:
            trees = [[]]
            for _label, _folder, rows in groups.values():
                for row in rows:
                    if not row[1] and trees[-1]:
                        trees.append([])
                    trees[-1].append(row)
            # (the open, unsaved one first, then the newest; a sort keeps sessions' order where it's the same. Nothing
            # listed, a search that finds nothing say, leaves the one empty tree, which has no first session)
            trees = sorted(filter(None, trees), key=lambda t: (not t[0][0].get("unsaved"), -last_used(t[0][0])))
            groups = {}
            for rows in trees:
                key, label = date_group(last_used(rows[0][0]), now)
                groups.setdefault(key, [label, None, []])[2].extend(rows)
        # (days don't overlap: by day too, the latest activity of a top-level session puts them in order)
        order = sorted(groups.items(),
                       key=lambda kv: (kv[0] != here, -max(last_used(i) for i, depth, _ in kv[1][2] if not depth)))
        out = [(k, label, folder, rows) for k, (label, folder, rows) in order]
        gone = [i for i in self.sessions if i["id"] in archived
                and (not q or q in (i.get("title") or "").lower())]
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
                # (its folder: by day, and on a chat from another folder under one of this folder's)
                elsewhere = depth and session_group(info.get("folder", info.get("cwd")))[0] != key
                row = self.session_row(info, depth, last, where=by_date or elsewhere)
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
        box = widgets.group_header(short_group_label(key, label), collapsed, lambda: self.toggle_folder(key), plus,
                                   tooltip=label, current=is_here)
        box.set_margin_top(6)
        box.set_margin_start(4)
        row = Gtk.ListBoxRow(child=box, activatable=False, selectable=False)
        row.folder, row.key = folder, key
        return row

    def session_row(self, info, depth=0, last=True, where=False):
        """Title ··· [folder] [worktree] [dot] — one line; the folder when grouped by day (where), the worktree's
        name for a worktree's chat, the dot only when something's happening (working, waiting for an answer,
        finished and waiting for input). Forks sit under their session like files in a tree."""
        box = Gtk.Box(spacing=6, margin_start=6 + depth * TreeList.INDENT, margin_end=4)
        is_open = info.get("unsaved") or info["id"] == self.win.session_id
        dot = StatusDot(self.win.session_state() if is_open else self.file_state(info))
        label = info.get("fork_label") if depth else None
        title = Gtk.Label(label=label or info.get("title") or "(untitled)", xalign=0,
                          ellipsize=Pango.EllipsizeMode.END, hexpand=True)
        if depth:
            title.add_css_class("fork-title")
        box.append(title)
        if where:  # (grouped by day: the folder it's in)
            place = Gtk.Label(label=short_group_label(*session_group(info.get("folder", info.get("cwd")))),
                              ellipsize=Pango.EllipsizeMode.END, max_width_chars=12, valign=Gtk.Align.CENTER)
            place.add_css_class("session-place")
            box.append(place)
        if tree := info.get("worktree"):  # (a worktree's chat, listed under its repository)
            tag = Gtk.Label(label=os.path.basename(tree), ellipsize=Pango.EllipsizeMode.MIDDLE, max_width_chars=14,
                            valign=Gtk.Align.CENTER)
            tag.add_css_class("worktree-tag")
            box.append(tag)
        box.append(dot)
        tip = [info.get("title") or "", info.get("cwd") or ""]
        if tree:
            tip.append(f"Worktree {os.path.basename(tree)} of {pretty_path(info.get('folder') or '')}")
        if info.get("source"):
            tip.append(info["source"])
        if info.get("copies"):
            tip.append(f"{info['copies']} earlier cop{'y' if info['copies'] == 1 else 'ies'} of this conversation hidden")
        row = Gtk.ListBoxRow(child=box, tooltip_text="\n".join(t for t in tip if t))
        row.info, row.dot, row.depth, row.tree_anchor = info, dot, depth, title
        if not info.get("unsaved"):
            click = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
            click.connect("pressed", lambda g, n, x, y: self.show_menu(row, x, y))
            row.add_controller(click)
        return row

    # ---- right-click menu / F2: rename, archive, move to trash
    def find_info(self, sid):
        return next((i for i in self.sessions if i["id"] == sid), None)

    def show_menu(self, row, x, y):
        info, sid = row.info, GLib.Variant.new_string(row.info["id"])
        menu = Gio.Menu()
        for label, action in (("Rename…", "rename"),
                              ("Unarchive" if info["id"] in self.archived_ids() else "Archive",
                               "unarchive" if info["id"] in self.archived_ids() else "archive"),
                              ("Move to Trash…", "trash")):
            item = Gio.MenuItem.new(label, None)
            item.set_action_and_target_value(f"sb.{action}", sid)
            menu.append_item(item)
        pop = Gtk.PopoverMenu(menu_model=menu, has_arrow=False)
        pop.set_parent(row)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        pop.set_pointing_to(rect)
        pop.connect("closed", lambda p: GLib.idle_add(p.unparent))
        self.menu = pop
        pop.popup()

    def on_key(self, _ctl, keyval, _code, _state):
        if keyval == Gdk.KEY_F2:
            row = self.list.get_focus_child() or self.list.get_selected_row()
            if row is not None and getattr(row, "info", None) and not row.info.get("unsaved"):
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
        if info["id"] == win.session_id and win.proc and not win.resume_pending:
            win.request({"subtype": "rename_session", "title": title}, lambda _r: None)  # the live session
        else:  # what the CLI itself writes for a rename
            with open(info["path"], "a", encoding="utf-8") as f:
                f.write(json.dumps({"type": "custom-title", "customTitle": title, "sessionId": info["id"]},
                                   separators=(",", ":")) + "\n")
        if info["id"] == win.session_id:
            win.show_title(title)
        self.refresh()

    def archive(self, info):
        if info:
            st = load_settings()
            save_settings(archived=sorted(set(st.get("archived", [])) | {info["id"]}),
                          unarchived=sorted(set(st.get("unarchived", [])) - {info["id"]}))
            self.fill()

    def unarchive(self, info):
        if info:
            st = load_settings()  # (archived in Claude Desktop: shown here anyway from now on)
            save_settings(archived=sorted(set(st.get("archived", [])) - {info["id"]}),
                          unarchived=sorted(set(st.get("unarchived", [])) | {info["id"]}))
            self.fill()

    def ask_trash(self, info):
        if info is None:
            return
        n = info.get("copies", 0)
        dlg = Adw.AlertDialog(heading=f"Move “{info.get('title') or 'this session'}” to the trash?",
                              body="Its transcript" + (f" and its {n} earlier cop{'y' if n == 1 else 'ies'}"
                                                       if n else "") +
                                   " will be moved to your trash, where you can restore it.",
                              default_response="cancel", close_response="cancel")
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("trash", "Move to Trash")
        dlg.set_response_appearance("trash", Adw.ResponseAppearance.DESTRUCTIVE)
        dlg.connect("response", lambda d, r: r == "trash" and self.trash(info))
        self.dialog = dlg
        dlg.present(self.win)

    def trash(self, info):
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

    def set_state(self, state):
        """The open session's dot (the others follow their files: see file_state)."""
        row = self.list.get_selected_row()
        if row is not None and getattr(row, "dot", None) is not None:
            row.dot.set_state(state)

    def file_state(self, info):
        """A session baude isn't running: working while another program (Claude Desktop, a
        terminal) is in the middle of a turn in it, done for a minute after, else inactive."""
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
        rows = {r.info["path"]: r for r in self.session_rows()}
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
        if info is None or info.get("unsaved") or info["id"] == self.win.session_id:
            return
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
        if not first:
            q = Gtk.Label(label=text, xalign=0, wrap=True)
            q.add_css_class("side-followup")
            self.body.append(q)
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

class SidePanel(Gtk.Box):
    """Right-hand pane: subagents (each with its own transcript) and background processes
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
        self.detail_holder = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
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
        """A subagent's tool card points at its transcript here."""
        btn = Gtk.Button(label="View subagent in side panel", halign=Gtk.Align.START)
        btn.add_css_class("flat")
        btn.add_css_class("link-button")
        btn.connect("clicked", lambda *_: (self.win.show_panel(True, "agents"),
                                           self.open_agent(task.get("tool_use_id"))))
        card.result_box.append(btn)
        card.show_output()

    def update_task(self, task):
        if task.get("task_type") == "local_agent":
            self.update_agent(task)
        elif task.get("task_type"):
            self.update_process(task)

    @staticmethod
    def describe(task):
        status = task.get("status", "running")
        usage = task.get("usage") or {}
        bits = [status]
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
                          margin_top=6, margin_bottom=12)
            box.add_css_class("zoomable")
            if task.get("prompt"):
                box.append(self.win.user_bubble(task["prompt"], []))
            self.agent_boxes[tid] = box
            if parent is not None:  # right under its parent (newest first, like the top level)
                self.agent_list.insert(row, parent.get_index() + 1)
            else:
                self.agent_list.prepend(row)
        row.set_title(html.escape(task.get("description") or "Subagent"))
        row.set_subtitle(html.escape(self.describe(task)))
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
        row.set_subtitle(html.escape(self.describe(task)))
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
_REF_WHERE = re.compile(r'"(cwd|file_path|notebook_path|command|name)":\s*"((?:[^"\\]|\\.)*)"')
_REF_CD = re.compile(r"(?:^|&&|;|\|\||\n|\(|\{|\bthen\b|\bdo\b)\s*(?:cd|pushd)\s+(\"[^\"]+\"|'[^']+'|(?:\\ |[^\s;&|()])+)")
_REF_GIT_C = re.compile(r"\bgit\s+-C\s+(\"[^\"]+\"|'[^']+'|(?:\\ |\S)+)\s+(?:commit|push|pull|merge|rebase|checkout|switch|"
                        r"cherry-pick|reset|revert|add|rm|mv|stash|tag|am|apply|restore)\b")
_REF_WRITERS = ("Edit", "Write", "MultiEdit", "NotebookEdit")


def transcript_folders(path):
    """Where a chat worked, in its transcript's order, each folder once (the most recent last): [(folder, acted)]. The
    folder each message ran in (acted False: prompts are recorded there whatever the chat does), and, acted, the
    folders its commands cd into or change with git -C (cd ../wt && ..., git -C ../wt commit), and the folders of the
    files it edits or writes. Reading a file somewhere isn't working there. A worktree beside the repository is outside
    the project, so Claude Code puts the shell back after each command there, and only the commands and edits say the
    chat worked in it. Kept per file size: a growing transcript is read again only once it has grown, and then only
    what it grew by, a line at a time (they get to hundreds of MB, and this runs as they grow)."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return []
    hit = _FOLDERS.get(path)
    if hit and hit[0] == size:
        return hit[1]
    fresh = {"offset": 0, "order": {}, "cwd": None, "writing": False, "last": None}
    st = hit[2] if hit and size > hit[2]["offset"] else fresh

    def seen(d, acted):  # (a folder acted in and one only run in are two entries, each where it was last)
        if d and os.path.isabs(d):
            key = (os.path.normpath(d), acted)
            order.pop(key, None)
            order[key] = True

    def target(t):
        d = os.path.expanduser(t.strip("\"'").replace("\\ ", " "))
        return d if os.path.isabs(d) else os.path.join(st["cwd"], d) if st["cwd"] else None
    try:
        with open(path, "rb") as f:
            if st["last"] is not None:  # (carrying on: still the file read, its last line where it was?)
                f.seek(st["last"][0])
                if _line_sum(f.readline()) != st["last"][1]:
                    st = fresh  # (rewritten: from the start)
            order = st["order"]
            f.seek(st["offset"])
            for raw in f:
                if not raw.endswith(b"\n"):
                    break  # (a line still being written: read next time)
                st["last"] = (st["offset"], _line_sum(raw))
                st["offset"] += len(raw)
                for m in _REF_WHERE.finditer(raw.decode("utf-8", "replace")):
                    try:
                        value = json.loads(f'"{m[2]}"')
                    except ValueError:
                        continue
                    if m[1] == "name":
                        st["writing"] = value in _REF_WRITERS  # (the tool whose input follows)
                    elif m[1] == "cwd":
                        st["cwd"] = value
                        seen(value, False)
                    elif m[1] == "command":
                        for t in _REF_CD.findall(value) + _REF_GIT_C.findall(value):
                            seen(target(t), True)
                    elif st["writing"] and os.path.isabs(value):
                        seen(os.path.dirname(value), True)
                        st["writing"] = False
    except OSError:
        return []
    _FOLDERS[path] = (size, list(order), st)
    return _FOLDERS[path][1]


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
    for d, acted in reversed(transcript_folders(transcript)):
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


def in_code(widget):
    """Whether a widget is on the Code page (its header too) or in another CodeView (the Kubernetes view's YAML):
    where Ctrl+scroll and Ctrl +/−/0 size the code rather than the chat."""
    while widget is not None and not isinstance(widget, (CodePage, CodeView)):
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
    """Menu → Settings… (⌘, on macOS, Ctrl+, elsewhere): full-width text, Baude in the platform's list of apps,
    feedback, and the sizes, 1.0 the usual: the text's and the icons', all of them in every window (apply_scales), and
    the chat's zoom that ⌘0 / Ctrl+0 goes back to. Each takes as it's changed and is kept in settings.json. (The
    switches are the window's actions, full-width's the menu's too, so the two agree; the handlers are given the row,
    never a closure over this dialog: see discard)"""

    def __init__(self, win):
        super().__init__(title="Settings")
        page = widgets.settings_page()
        group = Adw.PreferencesGroup()
        group.add(self.switch("Full-width text", None, "win.fullwidth"))
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


class BaudeWindow(Adw.ApplicationWindow, FeedbackHost):
    def __init__(self, app, opts):
        super().__init__(application=app, title="Baude", default_width=900, default_height=800)
        self.cwd = self.run_cwd = None  # set through set_cwd() once the header button exists
        self.proc = None
        self.session_id = opts.resume
        self.busy = False
        self.total_cost = 0.0
        self.always_allow = set()
        self.attachments = []
        self.chat_sent, self.listed_begun = False, None  # (the sidebar lists the open chat once it's begun)
        self.pending = {}          # control request id -> callback(response)
        self.tasks = {}            # background task id -> latest info (commands and subagents)
        self.waiting_cards = set()  # permission prompts not answered yet
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
        vadj = self.scroller.get_vadjustment()
        vadj.connect("value-changed", self.on_scrolled)
        wheel = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL)
        wheel.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        wheel.connect("scroll", self.on_reader_scroll)
        self.scroller.add_controller(wheel)
        # (its own scrolling and glide, each a step from where the view is: GTK's glide undid the view's moving by a
        # chunk of history drawn above it, mid-glide)
        self.scroll_glide = widgets.ScrollGlide(self.scroller, lambda: bool(self.history and self.history.more))
        self.connect("realize", lambda *_: self.get_frame_clock().connect_after("layout", self.on_layout))

        # composer (widgets.Composer: Record's too)
        self.attach_btn = Gtk.Button(icon_name="list-add-symbolic",
                                     tooltip_text="Attach images, PDFs or files (or paste / drop them)")
        self.attach_btn.add_css_class("flat")
        self.attach_btn.connect("clicked", self.on_pick_attachments)
        box_overlay = self.composer_box = Composer(
            buttons=[self.attach_btn],
            send_tip="Send (Enter, or Ctrl+Enter inside a code block)\nNew line: Shift+Enter")
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
        composer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                           margin_start=12, margin_end=12, margin_bottom=6)
        composer.append(self.attach_strip)
        composer.append(box_overlay)
        self.composer, self.read_only = composer, False  # (☰ → Read-only session hides it: apply_read_only)
        self.set_send_button(busy=False)

        self.status = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, hexpand=True)
        self.ctx_label = Gtk.Label(label="context —", xalign=1, ellipsize=Pango.EllipsizeMode.END)
        self.ctx_bar = ContextMeter(min_value=0, max_value=1, value=0, valign=Gtk.Align.CENTER)
        self.ctx_bar.add_offset_value("ctx-mid", 0.6)
        self.ctx_bar.add_offset_value("ctx-high", 0.85)
        ctx = Gtk.Box(spacing=8, tooltip_text="Context window usage")
        ctx.append(self.ctx_label)
        ctx.append(self.ctx_bar)
        self.ctx_box = ctx
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
        # drag the rule to resize the panel
        self.panel.set_visible(False)
        self.panel.set_size_request(320, -1)
        self.split = paned_grip(Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL,
                                          start_child=widgets.Squeezable(self.tabs), end_child=self.panel,
                                          resize_start_child=True, resize_end_child=False,
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
        self.connect("close-request", lambda *_: (self.proc and self.proc.kill(),
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
        restore = take_restart_state(getattr(opts, "restore", None))
        if restore:
            self.session_id = restore.get("session_id") or self.session_id
            self.model, self.mode = restore.get("model") or self.model, restore.get("mode") or self.mode
        if self.session_id and (info := find_session(self.session_id)):
            self.view_session(info["path"])
        self.poll_source = GLib.timeout_add(1000, self.poll_tail)
        if restore:
            self.apply_chat_signal()  # (the restored chat's own switches)
            self.apply_restart_state(restore)
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
            "at_bottom": self.stick_bottom,
            "from_bottom": adj.get_upper() - adj.get_page_size() - adj.get_value(),
            "shown": self.history.shown if self.history else 0,
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
        if self.history:
            while self.history.unshown and self.history.shown < state.get("shown", 0):
                self.show_earlier()
        self.stick_bottom = bool(state.get("at_bottom", True))
        if not self.stick_bottom:
            # hold the place (measured from the bottom) while the history lays out and images load
            self.restore_scroll = (float(state.get("from_bottom") or 0), time.monotonic() + 2.0)

    def restart_app(self):
        """☰ → Restart Baude: every window (BaudeApp.restart)."""
        self.get_application().restart()

    def idle(self):
        """Nothing a restart would cut short or take from under you (update.Updater): no turn running or waiting on
        you, no task of its running, no side question being answered or with a follow-up written to it (those don't come
        back; what's typed elsewhere does: restart_state), no dialog or capture open, and quiet (not the window you're in,
        or nothing typed, clicked or scrolled in it for a minute)."""
        bar = getattr(self, "capture_bar", None)
        return (not self.busy and not self.waiting_cards and not (self.proc is not None and self.running_tasks())
                and self.get_visible_dialog() is None and not (bar is not None and bar.get_reveal_child())
                and not any(getattr(sq, "busy", False) or sq.follow.get_text().strip()
                            for sq in self.panel.side_questions())
                and update.quiet(self))  # (the sidebar's dot, session_state, says the same of the first three)

    # ---- helpers
    def short_cwd(self):
        tree = main_folder(self.run_cwd)[1]
        return pretty_path(self.cwd) + (f" (worktree {os.path.basename(tree)})" if tree else "")

    def reset_stream_state(self):
        self.blocks = {}         # stream index -> widget for current message
        self.tool_cards = {}     # tool_use_id -> ToolCard
        self.tool_parent = {}    # tool_use_id -> the subagent's call it ran inside (nested agents)
        self.streamed_ids = set()  # message ids we already rendered via stream events

    def add(self, widget, parent_tool=None):
        """Append to the chat, or to a subagent's transcript (side panel, else inside its card).
        Consecutive tool calls go together in a ToolGroup."""
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
        return task_note_widget(note, self.tool_cards.get(note.get("tool_use_id")), self.jump_to_card)

    def jump_to_card(self, card):
        """Scroll the chat back to a card (opened, if it's a line in a run of calls) and flash it."""
        p = card.get_parent()
        while p is not None and p is not self.transcript:
            p = p.get_parent()
        if p is None:
            return
        if card.line:
            card.open_line()
        self.stick_bottom = False
        card.add_css_class("flash")
        GLib.timeout_add(1400, lambda: card.remove_css_class("flash") and False)

        def scroll():
            ok, b = card.compute_bounds(self.transcript)
            if ok:
                adj = self.scroller.get_vadjustment()
                adj.set_value(max(0, b.get_y() - 48))
            return False
        GLib.timeout_add(160, scroll)  # after an opened line has laid out

    def note(self, text, parent_tool=None):
        return self.add(sel_label(html.escape(text), css="sysnote"), parent_tool)

    def on_scrolled(self, adj):
        # near the top of what's drawn: draw the previous chunk (keep_place holds the view still)
        if self.history and self.history.more and adj.get_value() < adj.get_page_size() \
                and not getattr(self, "earlier_soon", None) and getattr(self, "keep_place", None) is None:
            self.earlier_soon = GLib.idle_add(self.auto_earlier)

    stick_bottom = property(lambda self: self.follow.stick, lambda self, on: setattr(self.follow, "stick", on))

    def on_reader_scroll(self, *_):
        self.restore_scroll = None  # the reader is scrolling: a restart's saved place no longer applies
        return False

    def on_layout(self, _clock):
        """Right after each layout pass: what's being held in place first, else the newest content in view."""
        # (a zoom, or a restart's place put back: those hold the view themselves, this one would fight them)
        held = self.hold_view_anchor() if self.zoom_job is None and self.restore_scroll is None else None
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
        self.follow.laid_out()
        if held is not True:  # (moved this pass: what's in view is found once it's laid out where it's moved to)
            self.note_view_anchor()

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
                return None if widget is hist or widget is getattr(self, "earlier_btn", None) else widget
            widget = parent
        return None

    def note_view_anchor(self):
        self.view_anchor = None
        if self.stick_bottom or self.transcript.get_width() <= 0:
            return
        w, page = self.scroller.get_width(), self.scroller.get_height()
        for y in range(1, max(2, page), 12):  # (down from the top, past the gaps between messages)
            for x in (0.5, 0.3, 0.7, 0.15, 0.85):  # (and across: a reply's at the left, your message at the right)
                if (msg := self.chat_message_of(self.scroller.pick(w * x, y, Gtk.PickFlags.DEFAULT))) is not None:
                    ok, b = msg.compute_bounds(self.transcript)
                    if ok:
                        self.view_anchor = (msg, b.get_y(), self.transcript.get_width())
                    return

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
        adj = self.scroller.get_vadjustment()
        adj.set_value(adj.get_value() + b.get_y() - y)
        self.view_anchor = (msg, b.get_y(), width)  # (held to it till the view's laid out where it's been moved)
        return True

    def set_busy(self, busy):
        self.busy = busy
        if not busy and self.proc is not None:
            self.turn_finished = True
        self.set_send_button(busy)
        self.update_status()
        self.update_session_state()

    def session_state(self):
        """working / waiting / done / inactive, for the sidebar's dot."""
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
    def request(self, req, callback):
        if self.proc:
            self.pending[self.proc.control(req)] = callback

    def refresh_context(self):
        self.request({"subtype": "get_context_usage"}, self.show_context)

    def show_context(self, usage):
        used, total = usage.get("totalTokens") or 0, usage.get("maxTokens") or 0
        if not total:
            return
        frac = min(1.0, used / total)
        self.ctx_label.set_text(f"context {fmt_tokens(used)} / {fmt_tokens(total)} · {frac:.0%}")
        self.ctx_bar.set_value(frac)
        lines = [f"{c['name']}: {fmt_tokens(c.get('tokens', 0))}" for c in usage.get("categories") or []
                 if c.get("tokens") and c.get("kind") != "deferred"]
        self.ctx_box.set_tooltip_text("Context window usage\n" + "\n".join(lines))

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
    def start_proc(self):
        try:
            # while a session is only being viewed, claude runs fresh (it supplies models and commands
            # but writes nothing); it resumes the session when the first message is sent
            resume = None if self.resume_pending else self.session_id
            if self.model == APPLE_MODEL["value"]:  # (Apple's model: apple-claude, Manual, no effort, a chat of its own)
                if (exe := apple_exe()) is None:
                    self.note("Apple Foundation Model: apple-claude isn't built yet (swift build -c release, in apple-claude/)")
                    self.proc = None
                    self.update_status()
                    return
                self.proc = ClaudeProc(self.on_msg, self.on_exit, self.run_cwd, self.model, "default", None, None, exe=exe)
            elif model_program(self.model) == "pi":  # (a model on this Mac: pi-claude, Manual, no effort)
                exe = os.environ.get("BAUDE_PI_CLAUDE") or os.path.join(PI_CLAUDE, "pi-claude")
                self.proc = ClaudeProc(self.on_msg, self.on_exit, self.run_cwd, self.model, "default", resume,
                                       self.local_effort if self.effort_dd.get_sensitive() else None, exe=exe)
            else:
                self.proc = ClaudeProc(self.on_msg, self.on_exit, self.run_cwd, self.model, self.mode,
                                       resume, self.effort if self.effort_dd.get_sensitive() else None)
            self.proc.resumed = resume
            self.pending.clear()
        except OSError as e:
            self.note(f"failed to start claude: {e}")
            self.proc = None
        self.update_status()

    def restart_proc(self, resume=True):
        if self.proc:
            p, self.proc = self.proc, None
            p.on_exit = lambda *_: None
            p.kill()
            self.remember_own_writes()
        if not resume:
            self.session_id = None
            self.reset_stream_state()
        else:
            # the same conversation carries on: keep tool_use_id -> card, so results, task endings
            # and subagent messages that arrive later still find their cards (only the half-drawn
            # stream of the old process is dropped)
            self.blocks, self.streamed_ids = {}, set()
        self.waiting_cards.clear()
        self.turn_finished = False
        self.set_busy(False)
        self.start_proc()

    def clear_transcript(self, closing=False):
        self.remember_place()  # (to open it there again)
        self.restore_scroll = None
        self.chat_sent, self.listed_begun = False, None  # (another chat: nothing sent in it yet)
        while (c := self.transcript.get_first_child()) is not None:
            self.transcript.remove(c)
            discard(c)
        self.total_cost = 0.0
        self.ctx_label.set_text("context —")
        self.ctx_bar.set_value(0)
        self.show_title(None)
        self.tasks.clear()
        self.sub_tails.clear()
        self.artifacts.clear()
        if not closing:  # (they're every window's: one closing leaves the others theirs)
            ARTIFACT_TITLES.clear()
        self.ref_surfaces.clear()
        self.reply_views.clear()
        self.orphan_results.clear()  # (results whose calls the chat before never drew: output, images and all)
        self.history = None
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
        run_in = self.norm_dir(run_in) if run_in else self.cwd if os.path.isdir(self.cwd) else self.run_cwd
        if run_in != self.run_cwd:
            self.switching_chat = True  # (the new chat's own switches come below)
            self.set_cwd(run_in)
        self.remember_own_writes()
        self.resume_pending, self.view_path = False, None
        self.tail.stop()
        self.update_banner()
        self.clear_transcript()
        self.read_only_mode()
        self.restart_proc(resume=False)
        self.signal_pending, self.switching_chat = None, False
        self.apply_chat_signal()  # (a new chat: the defaults for new chats)
        self.apply_read_only()
        self.sidebar.fill()

    # ---- read-only sessions (☰ → Read-only session): no text box, for a chat you only watch (tailing one with
    # Signal on, say); the status line, with the context it uses, stays. Remembered per session.
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
        self.composer.set_visible(not on)
        if (action := self.lookup_action("readonly")) is not None:
            action.set_state(GLib.Variant.new_boolean(on))
        if on and self.input.has_focus():
            self.scroller.grab_focus()

    def on_exit(self, code):
        self.note(f"claude exited ({code}). Send a message to restart / resume.")
        self.proc = None
        self.set_busy(False)

    # ---- UI events
    def on_key(self, _ctl, keyval, _code, state):
        if self.completer.handle_key(keyval):
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
        if self.proc is None:
            self.start_proc()
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
        if model_program(value) != model_program(was):  # (Claude's to Apple's, say: another program)
            self.note(f"model → {self.model_dd.label()}: a new conversation")
            self.restart_proc(resume=False)
            return
        if model_program(value) == "pi" and value != was:  # (pi-claude serves one model: pi's session goes on)
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
            self.proc.control({"subtype": "set_permission_mode", "mode": value})
            self.note(f"permission mode → {self.mode_dd.label()}")

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
        on Claude's last used (or claude's own default): never Apple's or one on this Mac, which can't carry it on."""
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
        if models and all(model_program(m["value"]) != "claude" for m in models):
            models = getattr(self, "claude_models", [])
        else:
            self.claude_models = models
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
            self.mode_dd.select(mode)

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
        if self.busy:
            self.note("stop the current turn before starting another session")
            return
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
            elif kind == "task_notification" and card.state in ("running", "background"):
                # not on task_updated: it comes first, without the summary and output file
                card.finish_task(task)
        if kind == "task_notification" and not task.get("noted") \
                and task.get("tool_use_id") not in self.tool_parent:  # (a nested agent's card says so)
            task["noted"] = True
            self.add(self.task_note(task))
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
                    self.add(sel_label(f'<span foreground="red">{html.escape(c["text"])}</span>'), parent)
            return
        self.render_assistant(m.get("content", []), parent, streamed=m.get("id") in self.streamed_ids)

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

    def queue_history_refs(self, items):
        """Replies earlier in a long session, not drawn yet: looked up too, newest first, after the ones on
        screen (so the Signal page covers the whole session, and links are ready when you scroll up). items:
        newest first (HistoryIndex.earlier_replies, read from the transcript only once it's needed here)."""
        if self.signal is None:
            return
        for kind, content, *rest in items:
            if kind != "assistant" or (rest and rest[0]):
                continue
            for c in content if isinstance(content, list) else [{"type": "text", "text": str(content)}]:
                if isinstance(c, dict) and (text := c.get("text") or c.get("thinking") or "").strip() and \
                        c.get("type") in ("text", "thinking"):
                    self.queue_refs(text, ref_epoch(rest[1]) if len(rest) > 1 else None)

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
        """Ctrl +/−/0 with the focus on the Code page (or in the Kubernetes view's YAML): the code's size, steps up
        or down (0: 100% again). False elsewhere, where they size the chat."""
        if not in_code(self.get_focus()):
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
        if win.signal is not None:
            win.signal.github_changed()

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
        task["noted"] = True  # the transcript already shows its own note for this ending
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

    # ---- history
    # items drawn when a session opens, and per earlier chunk (loaded as you scroll near the top, or by
    # "Show earlier messages"): only the last screen is in view, and each item costs layout time
    HISTORY_CHUNK = 60
    PLACES = {}  # session id -> where its chat was left (chat_place): every window's, kept over a restart
    MAX_PLACES = 500

    def chat_place(self):
        """Where the chat is scrolled to, for opening it there again (open_at): measured from its end (what's added at
        its top as you scroll up doesn't move it), with how many of its items were drawn. None if it was never laid out."""
        adj = self.scroller.get_vadjustment()
        if adj.get_page_size() <= 0:
            return None
        from_bottom = max(0.0, adj.get_upper() - adj.get_page_size() - adj.get_value())
        return {"from_bottom": from_bottom, "at_end": self.stick_bottom or from_bottom <= 1,
                "shown": self.history.shown if self.history else 0}

    def remember_place(self):
        """The open chat's place remembered (chat_place), as it's left for another."""
        if self.session_id and (place := self.chat_place()) is not None:
            self.PLACES.pop(self.session_id, None)
            self.PLACES[self.session_id] = place
            while len(self.PLACES) > self.MAX_PLACES:  # (the longest unopened go)
                del self.PLACES[next(iter(self.PLACES))]

    def open_at(self, place):
        """A chat just drawn shown where it was left (place: chat_place), its earlier parts drawn back as they were;
        never left before, at its end. There at once and held there while it lays out (on_layout: images and code
        come in sizes later), so it doesn't glide down to its end or move under you."""
        if place and not place.get("at_end"):
            while self.history and self.history.unshown and self.history.shown < place.get("shown", 0):
                self.show_earlier()
            self.stick_bottom = False
            from_bottom = float(place.get("from_bottom") or 0)
        else:
            self.stick_bottom, from_bottom = True, 0.0
        self.restore_scroll = (from_bottom, time.monotonic() + 2.0)

    def load_history(self, path, live=False):
        """Draw the newest part of a saved session; older parts load on demand from the top."""
        self.history = HistoryIndex(path)
        items = self.history.load(self.HISTORY_CHUNK)
        self.history_path = path
        self.earlier_btn = Gtk.Button(halign=Gtk.Align.CENTER, visible=False)
        self.earlier_btn.add_css_class("pill")
        self.earlier_btn.connect("clicked", lambda *_: self.show_earlier())
        self.add(self.earlier_btn)
        self.history_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, visible=False)  # (shown once one's drawn)
        self.history_box.is_history, self.chunk_slots = True, None
        self.add(self.history_box)
        self.make_chunk_slots()
        self.render_history(items, final=not live)
        if self.history.todo:  # (the start of a long one: indexed in idle time, then counted and read by Signal)
            GLib.idle_add(self.index_history, self.history, priority=GLib.PRIORITY_LOW)
        else:
            self.queue_history_refs(self.history.earlier_replies())
        self.update_earlier_btn()
        self.resumed_note = bool(items)  # "— resumed —" goes in when you first write, not on opening
        self.open_at(self.PLACES.get(self.session_id))

    def update_earlier_btn(self):
        n = self.history.count() if self.history else 0
        self.earlier_btn.set_label("Show earlier messages" if n is None else f"Show earlier messages ({n} more)")
        self.earlier_btn.set_visible(bool(self.history and self.history.more))

    def make_chunk_slots(self):
        """The earlier chunks' places, made once it's known how many (empty, so of no size), filled from the bottom
        up as you scroll: a chunk put in above what's drawn moved everything below it, and GTK styled all of that
        again (a quarter of a second a chunk, deep into a long chat)."""
        if self.chunk_slots is not None or (n := self.history.count()) is None:
            return
        self.chunk_slots = [Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
                            for _ in range(-(-n // self.HISTORY_CHUNK))]
        for slot in reversed(self.chunk_slots):  # (the first: the lowest, next to what's drawn on opening)
            self.history_box.append(slot)

    def index_history(self, history):
        """A slice of indexing the start of a long chat (HistoryIndex.index_some), between frames."""
        if history is not self.history:
            return False  # (another chat now)
        if history.index_some():
            return True
        self.make_chunk_slots()
        self.update_earlier_btn()
        self.queue_history_refs(history.earlier_replies())
        return False

    def auto_earlier(self):
        self.earlier_soon = None
        if self.history and self.history.more and not self.stick_bottom:
            self.show_earlier()
        return False

    def show_earlier(self):
        """Draw the previous chunk above what's shown, keeping the view where it was."""
        if not (self.history and self.history.more):
            return
        self.history.finish()  # (scrolled up before the start of a long chat was indexed: now)
        if not self.history.unshown:  # (nothing in it to draw after all)
            self.update_earlier_btn()
            return
        self.make_chunk_slots()
        items = self.history.earlier(self.HISTORY_CHUNK)  # (read from the transcript again: see HistoryIndex)
        drawn = self.history_box.get_visible()
        if self.chunk_slots:
            box = self.chunk_slots.pop(0)
        else:  # (more than there were places: the transcript was rewritten since it opened)
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
            self.history_box.prepend(box)
        box.set_margin_bottom(10 if drawn else 0)  # (the chunk below it; the lowest has the chat's own spacing)
        adj = self.scroller.get_vadjustment()
        before = adj.get_upper()
        self.render_target = box
        try:
            self.render_history(items)
        finally:
            self.render_target = None
        self.stick_bottom = False
        self.history_box.set_visible(True)
        self.update_earlier_btn()

        # once the chunk is laid out, shift the view by what was added above it (only once: later growth, like a reply
        # streaming in at the bottom, must not move it), right after the layout pass it's in (on_layout), so the frame
        # is drawn with it: at notify::upper the frame was drawn first, the view a chunk's height off (6000 px of
        # screenshots), and set back from where it was, not where it's been scrolled to since
        self.cancel_keep_place()
        # if the layout settles without a size change, don't leave it armed: fired much later (by
        # a zoom, a new message) it would throw the view off by the old chunk's height
        expire = GLib.timeout_add(1500, lambda: (self.cancel_keep_place(), False)[1])
        self.keep_place = (adj, before, expire)

    def hold_keep_place(self, held):
        """After a layout pass: the view moved by what a chunk drawn above added, once it's laid out (show_earlier),
        unless the message at the view's top has held it already (held: hold_view_anchor's)."""
        adj, before, _expire = self.keep_place
        if adj.get_upper() != before:
            self.cancel_keep_place()
            if held is None:
                adj.set_value(adj.get_value() + adj.get_upper() - before)

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
                self.add(self.task_note(content))
                self.task_from_note(content)
            elif kind == "compact_summary":  # (what the CLI wrote for itself on compacting: folded)
                fold = Gtk.Expander(label="Summary of the earlier conversation", child=MarkdownView(content))
                fold.add_css_class("sysnote")
                self.add(fold)
            elif kind == "ci_event":  # (what the desktop app says of a pull request it's watching: folded)
                fold = Gtk.Expander(label=content["title"], child=MarkdownView(content["text"]))
                fold.add_css_class("sysnote")
                self.add(fold)
            elif kind == "assistant" and rest and rest[0]:
                self.add(sel_label(f'<span foreground="red">{html.escape(_first_text(content) or "")}</span>'))
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
        # likewise background tasks a finished session never heard the end of
        if final:
            for task in self.tasks.values():
                if task.get("from_file") and task.get("status") == "running":
                    task["status"] = "interrupted"
                    card = self.tool_cards.get(task.get("tool_use_id"))
                    if card is not None and card.state in ("running", "background"):
                        card.set_state("interrupted")
                    self.panel.update_task(task)
            self.update_tasks_indicator()

    def show_sessions(self):
        self.sidebar_slide.set_shown(True)
        self.sidebar.focus_search()

    def resume_session(self, info):
        if self.busy:
            self.note("stop the current turn before switching sessions")
            return
        self.remember_own_writes()  # before the view switches to the other session
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
        self.total_cost = (cached_session_info(path) or {}).get("cost") or 0.0  # (claude's, as it last exited)
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
        """Is the viewed session in use by another program (Claude Desktop, a terminal)?"""
        return self.resume_pending and self.tail.in_use()

    def update_banner(self):
        """While another program has the session, say so and keep the input off: two writers
        would interleave two conversations in one transcript."""
        live = self.live_elsewhere()
        if live:
            self.banner.set_title("This session is currently active elsewhere")
        self.banner.set_revealed(live)
        self.composer_box.set_sensitive(not live)

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
        self.resume_pending, self.view_path = False, None
        self.tail.stop()
        self.update_banner()
        if self.resumed_note:
            self.note("— resumed —")
            self.resumed_note = False
        self.restart_proc(resume=True)

    def h_result(self, msg):
        GLib.idle_add(lambda: self.follow_checkout() and False)  # (the turn may have moved into a worktree)
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
            GLib.idle_add(lambda: self.restart_proc(resume=True) and False)
        self.refresh_context()
        self.sidebar.refresh()  # the session file just changed (first save, newer time)
        self.update_status(f"{msg.get('num_turns', 0)} turns · {msg.get('duration_ms', 0) / 1000:.1f}s"
                           f" · in {tok} / out {u.get('output_tokens', 0)} tok")

    def h_control_response(self, msg):
        r = msg.get("response", {})
        callback = self.pending.pop(r.get("request_id"), None)
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
            self.proc.respond(rid, {"behavior": "allow", "updatedInput": inp})
            return
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
        """A turn that failed for want of signing in: said, with Sign in (sign_in)."""
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
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        card.add_css_class("perm-card")
        self.waiting_cards.add(card)
        self.update_session_state()
        title, source = tool_display(name)
        what = f"{source}: {title}" if source else title
        summary = summarize_tool(name, inp)
        card.append(sel_label(f"<b>Allow {html.escape(what)}?</b>" +
                              (f"  <span alpha='60%'>{html.escape(summary)}</span>" if summary else "")))
        card.append(tool_input_view(name, inp))
        row = Gtk.Box(spacing=6)
        proc = self.proc

        def decide(_b, behavior, always=False):
            if always:
                self.always_allow.add(name)
            if behavior == "allow":
                proc.respond(rid, {"behavior": "allow", "updatedInput": inp})
            else:
                proc.respond(rid, {"behavior": "deny", "message": "The user denied this tool use."})
            card.get_parent() and card.get_parent().remove(card)
            self.waiting_cards.discard(card)
            self.update_session_state()
            decision = ("always allowed" if always else "allowed") if behavior == "allow" else "denied"
            if tool_use_id in self.tool_cards:
                self.tool_cards[tool_use_id].set_decision(decision)  # keeps tool groups together
            else:
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
        card.append(row)
        self.add(card)
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
            self.updater = update.Updater("baude", self.idle, self.restart)

    def windows(self):
        """Baude's windows, the one you were in last first."""
        return [w for w in self.get_windows() if isinstance(w, BaudeWindow)]

    def idle(self):
        """Every window idle (BaudeWindow.idle): a new version can be restarted onto."""
        return all(w.idle() for w in self.windows())

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
        save_info_cache(force=True)  # (as quitting does: exec skips do_shutdown)
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
        "banner shown": win.banner.get_revealed(), "input enabled": win.composer_box.get_sensitive(),
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
