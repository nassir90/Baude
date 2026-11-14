"""Claude Code's transcripts: where they are, what each one is (its title, folder, prompts, cost, when it was last typed
in) and the conversation it records, item by item as Baude draws it. No GTK: the headless server (baudelib.server)
reads them too."""
import array
import base64
import bisect
import collections
import json
import os
import re
import sys
import time


def ref_epoch(iso):
    if not iso:
        return None
    import datetime
    try:
        return datetime.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


_UNTYPED_STARTS = ("<task-notification>", "<ci-monitor-event>", "<side-sessions-event>", "<local-command-stdout>",
              "<local-command-stderr>")


def typed_at(rec):
    """When a transcript record is something the person typed, its ISO time: a prompt, or one sent while a turn ran
    (a queued_command); None for the rest: tool results, what claude or the app put in (a task's ending, a CI event, a
    scheduled run, another session's message). A chat's last one says when it was last used (Baude's sidebar and
    Record's lists go by it): the file's own time moves with every reply, rename and record claude keeps."""
    kind = rec.get("type")
    if kind == "attachment":
        a = rec.get("attachment") or {}
        if a.get("type") == "queued_command" and a.get("commandMode", "prompt") == "prompt" \
                and (a.get("origin") or {}).get("kind", "human") == "human":
            return a.get("timestamp") or rec.get("timestamp")
        return None
    if kind != "user" or rec.get("isMeta") or rec.get("isSidechain") or rec.get("isCompactSummary") \
            or rec.get("turnOrigin") in ("task_notification", "scheduled", "peer"):
        return None
    content = (rec.get("message") or {}).get("content")
    if isinstance(content, list):
        if any(isinstance(c, dict) and c.get("type") == "tool_result" for c in content):
            return None
        if any(isinstance(c, dict) and c.get("type") == "image" for c in content):
            return rec.get("timestamp")
        content = " ".join(c.get("text") or "" for c in content if isinstance(c, dict) and c.get("type") == "text")
    if not isinstance(content, str) or not content.strip() or content.lstrip().startswith(_UNTYPED_STARTS):
        return None
    return rec.get("timestamp")


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
    st = {**state, "uuids": set(state["uuids"]), "foreign": set(state["foreign"])} if state else _scan_state()
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


def _scan_state():
    """scan_session's tallies before any line's read."""
    return {"offset": 0, "title": None, "agent_name": None, "last_prompt": None, "relocated": None, "summary": None,
            "uuids": set(), "born": None, "foreign": set(), "cwd": None, "source": "", "prompts": 0,
            "first_prompt": None, "cost": 0.0, "said": None}


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
            when = typed_at(json.loads(line))
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
    if (when := typed_at(rec)) and when > (st.get("said") or ""):
        st["said"] = when
    if _is_prompt(rec):
        st["prompts"] += 1
        if st["first_prompt"] is None:
            st["first_prompt"] = _command_text(_first_text(rec["message"]["content"]))


SESSION_SOURCES = {"cli": "Claude Code (terminal)", "claude-desktop": "Claude Desktop",
                   "sdk-cli": "Baude / SDK", "sdk-ts": "Agent SDK", "sdk-py": "Agent SDK"}


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
    """Visible items from transcript lines: ('user'|'assistant', content, is_error, timestamp, uuid),
    ('tool_results', content) or ('artifact', info). Sidechain records are skipped unless
    sidechain (a subagent's own transcript is nothing but); meta records always are."""
    return [item for line in lines if (item := history_item(line, sidechain)) is not None]


def rewind_mark(line):
    """A revert's mark (claude's rewind_conversation: what came after the last record it keeps is dropped from the
    conversation, the transcript only ever appended to): (the uuid of that record, None for none kept), or None if the
    line isn't one."""
    if '"rewound"' not in line or '"last-prompt"' not in line:
        return None
    try:
        rec = json.loads(line)
    except ValueError:
        return None
    if not isinstance(rec, dict) or rec.get("type") != "last-prompt" or rec.get("rewound") is not True:
        return None
    return (rec.get("leafUuid"),)


def line_of_uuid(path, uuid, before):
    """Where the line of the record with this uuid starts, searched back from before (a revert's kept record): -1 for
    none kept (uuid None: all that's before is dropped); None if it isn't there."""
    if uuid is None:
        return -1
    needles = (f'"uuid":"{uuid}"'.encode(), f'"uuid": "{uuid}"'.encode())
    span, end = 1 << 20, before
    try:
        with open(path, "rb") as f:
            while end > 0:
                begin = max(0, end - span)
                f.seek(begin)
                block = f.read(end - begin + 200)  # (a needle across the block's end: read a little past it)
                found = max(block.rfind(n, 0, before - begin) for n in needles)  # (one starting before before)
                if found >= 0:
                    return begin + block.rfind(b"\n", 0, found) + 1
                end = begin
    except OSError:
        return None
    return None


def cut_after(starts, at, *others):
    """Drop the indexed items whose lines start after at (a revert's kept record): from starts and the arrays beside
    it (in order, as starts is). How many went."""
    i = bisect.bisect_right(starts, at)
    gone = len(starts) - i
    for a in (starts,) + others:
        del a[i:]
    return gone


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
    return rec["type"], content, rec.get("isApiErrorMessage", False), rec.get("timestamp"), rec.get("uuid")


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
        self.taken = []                 # [begin, end) ranges drawn out of order (take), still numbered among them
        self.taken_count = 0            # items take has read in all
        self.cuts = []                  # (kept, mark): reverts, what's between the two lines dropped (rewind_mark)

    @property
    def unshown(self):
        """How many items aren't drawn yet (once indexed: see more)."""
        self.finish()
        return len(self.starts) - sum(end - begin for begin, end in self.taken)

    @property
    def more(self):
        """Whether anything's left to draw: some items, or a part not indexed yet."""
        return bool(self.todo) or len(self.starts) > sum(end - begin for begin, end in self.taken)

    def count(self):
        """How many items aren't drawn yet; None while the start is still being indexed."""
        return None if self.todo else self.unshown

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
            newest, self.cuts = collections.deque(maxlen=keep), []
            try:
                with open(self.path, "rb") as f:
                    f.seek(begin)
                    at = first = begin + (len(f.readline()) if begin else 0)  # (a line cut in two: the part before's)
                    for raw in f:
                        line = raw.decode("utf-8", "replace")
                        if (item := history_item(line)) is not None:
                            starts.append(at)
                            sums.append(_line_sum(raw))
                            replies.append(item[0] == "assistant" and not item[2] and _has_text(item[1]))
                            newest.append(item)
                        elif (gone := self._cut(line, at, starts, sums, replies)) is not None:
                            for _ in range(min(gone, len(newest))):
                                newest.pop()
                        at += len(raw)
            except OSError:
                return []
            if len(starts) >= keep or not begin:  # (a revert's cut items: enough indexed, those it popped read below)
                break
            span *= 4
        if len(newest) < min(keep, len(starts)):  # (a cut popped the newest: the ones before them, from where they are)
            newest = self._read_at(starts[-keep:], sums[-keep:]) or []
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
                    line = raw.decode("utf-8", "replace")
                    if (item := history_item(line)) is not None:
                        if not self.dropped(at):
                            self.starts.append(at)
                            self.sums.append(_line_sum(raw))
                            self.replies.append(item[0] == "assistant" and not item[2] and _has_text(item[1]))
                    else:
                        self._cut(line, at, self.starts, self.sums, self.replies)
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

    def _cut(self, line, at, starts, *others):
        """A revert's mark at at (rewind_mark): what it drops, dropped from what's indexed (the items whose lines are
        between the record it keeps and it) and kept in cuts for what's indexed after (index_some: the start of the
        file, read after its end). How many items went; None if the line isn't a mark."""
        if (mark := rewind_mark(line)) is None or (kept := line_of_uuid(self.path, mark[0], at)) is None:
            return None
        self.cuts.append((kept, at))
        return cut_after(starts, kept, *others)

    def _read_at(self, starts, sums):
        """The items whose lines start at starts, parsed (the newest, after a cut: _tail); None if one isn't there."""
        items = []
        try:
            with open(self.path, "rb") as f:
                for start, line_sum in zip(starts, sums):
                    f.seek(start)
                    raw = f.readline()
                    if _line_sum(raw) != line_sum or (item := history_item(raw.decode("utf-8", "replace"))) is None:
                        return None
                    items.append(item)
        except OSError:
            return None
        return items

    def dropped(self, at):
        """Whether the item whose line starts at at was taken back by a revert (cuts)."""
        return any(kept < at < mark for kept, mark in self.cuts)

    def _index(self, keep):
        """(Re)read the whole file; all but the newest `keep` items are left undrawn, and those are returned."""
        import collections
        starts, sums, replies, newest = array.array("q"), array.array("q"), bytearray(), collections.deque(maxlen=keep)
        self.cuts = []
        try:
            with open(self.path, "rb") as f:
                at = 0
                for raw in f:  # (one line at a time: only the newest items stay parsed)
                    line = raw.decode("utf-8", "replace")
                    if (item := history_item(line)) is not None:
                        starts.append(at)
                        sums.append(_line_sum(raw))
                        replies.append(item[0] == "assistant" and not item[2] and _has_text(item[1]))
                        newest.append(item)
                    elif (gone := self._cut(line, at, starts, sums, replies)) is not None:
                        for _ in range(min(gone, len(newest))):
                            newest.pop()
                    at += len(raw)
        except OSError:
            pass
        undrawn = len(starts) - len(newest)
        self.oldest = sums[undrawn] if newest else None
        del starts[undrawn:], sums[undrawn:], replies[undrawn:]
        self.starts, self.sums, self.replies, self.todo, self.later = starts, sums, replies, None, None
        self.taken = []  # (numbered afresh)
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

    def reindex(self, drawn=()):
        """Index the transcript again: take found a line that isn't the one indexed (rewritten since it opened). drawn:
        the line sums of items drawn out of order (before the oldest drawn in order, which _reindex takes as undrawn),
        left out of what's still to draw."""
        self._reindex()
        if drawn:
            keep = [i for i in range(len(self.starts)) if self.sums[i] not in drawn]
            self.starts = array.array("q", (self.starts[i] for i in keep))
            self.sums = array.array("q", (self.sums[i] for i in keep))
            self.replies = bytearray(self.replies[i] for i in keep)

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
        begin = max(0, len(self.starts) - n)
        items = self._read(begin, len(self.starts))
        if items is None:
            self._reindex()
            begin = max(0, len(self.starts) - n)
            items = self._read(begin, len(self.starts))
        if items is None:  # (still not the file indexed, or gone: nothing earlier to draw)
            begin, items = 0, []
        else:
            self.shown += len(items)
            self.oldest = self.sums[begin] if items else self.oldest
        del self.starts[begin:], self.sums[begin:], self.replies[begin:]
        return items

    def take(self, begin, end):
        """Items begin..end of those not drawn yet, as they're numbered before any are taken (the window's places for
        the earlier chunks: BaudeWindow.make_chunk_slots): where their lines are, in order, each read as it's drawn
        (item_at); they count as drawn. Taken in any order (the chunk under the view first): what's before them keeps
        its number."""
        self.finish()
        end = min(end, len(self.starts))
        places = [(self.starts[i], self.sums[i]) for i in range(begin, end)]
        self.shown += len(places)
        self.taken_count += len(places)
        self.taken.append((begin, end))
        while (last := next((r for r in self.taken if r[1] == len(self.starts)), None)) is not None:
            self.taken.remove(last)  # (drawn up to the end of what's left: gone from it, as earlier's are)
            self.oldest = self.sums[last[0]] if last[0] < len(self.sums) else self.oldest
            del self.starts[last[0]:], self.sums[last[0]:], self.replies[last[0]:]
        return places

    def item_at(self, place):
        """The item a line take gave the place of, parsed; None if it isn't the line indexed (rewritten since)."""
        start, line_sum = place
        try:
            with open(self.path, "rb") as f:
                f.seek(start)
                raw = f.readline()
        except OSError:
            return None
        return history_item(raw.decode("utf-8", "replace")) if _line_sum(raw) == line_sum else None

    def earlier_replies(self):
        """Claude's replies among the items not drawn yet, newest first, each parsed as it's needed."""
        self.finish()
        try:
            with open(self.path, "rb") as f:
                for i in range(len(self.starts) - 1, -1, -1):
                    if i >= len(self.starts):  # (drawn since this began: take cut the index short)
                        continue
                    if self.replies[i] and not any(begin <= i < end for begin, end in self.taken):
                        f.seek(self.starts[i])
                        raw = f.readline()
                        if _line_sum(raw) != self.sums[i]:
                            return  # (rewritten: drawing finds where they went)
                        yield history_item(raw.decode("utf-8", "replace"))
        except OSError:
            return


class ChunkFill:
    """An earlier chunk of a chat being drawn into its place (BaudeWindow.fill_frame): where the items still to make are
    (HistoryIndex.take: each read as it's made), what's made but hidden till its turn (in order: shown from the bottom
    up), and the calls its items made (calls: the ones it ends as interrupted if they never got a result)."""

    def __init__(self, slot, places):
        self.slot, self.items, self.hidden, self.calls = slot, collections.deque(places), [], set()
        self.broken = False  # (a line that isn't the one indexed: the transcript was rewritten)


def read_history(path, limit=400):
    """The visible conversation of a transcript (see history_items). Returns (items, skipped)."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            items = history_items(f)
    except OSError:
        return [], 0
    skipped = max(0, len(items) - limit) if limit else 0
    return items[skipped:], skipped
