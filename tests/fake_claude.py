#!/usr/bin/env python3
"""Stand-in for `claude -p --input-format stream-json ...` used by the tests.

Speaks just enough of the stream-json protocol: answers `initialize` with
commands, models and the permission mode, and replays one scripted turn per user message (thinking,
streamed markdown, a Bash tool call that needs permission, the tool result,
a closing message and a `result`). Every line it receives is appended to
$FAKE_CLAUDE_LOG, plus a START line with its cwd and argv.
"""
import json
import re
import os
import subprocess
import sys
import tempfile
import threading
import time

SESSION = "fake-session-0001"
COMMANDS = [
    {"name": "debug", "description": "Enable debug logging", "argumentHint": "[issue]", "builtin": True},
    {"name": "deep-research", "description": "Fan-out web research", "argumentHint": "", "builtin": True},
    {"name": "design", "description": "Make a new Design artifact", "argumentHint": "[what]", "builtin": True},
    {"name": "clear", "description": "Clear conversation", "argumentHint": "", "builtin": True},
    {"name": "compact", "description": "Compact conversation", "argumentHint": "", "builtin": True},
    {"name": "code-review", "description": "Review the current diff", "argumentHint": "", "builtin": False},
]

LEVELS = ["low", "medium", "high", "xhigh", "max"]
MODELS = [
    {"value": "default", "resolvedModel": "claude-opus-5-5", "displayName": "Default (recommended)",
     "supportsEffort": True, "supportedEffortLevels": LEVELS},
    {"value": "opus", "resolvedModel": "claude-opus-5-5", "displayName": "Opus 5.5",
     "supportsEffort": True, "supportedEffortLevels": LEVELS},
    {"value": "sonnet", "resolvedModel": "claude-sonnet-5-5", "displayName": "Sonnet 5.5",
     "supportsEffort": True, "supportedEffortLevels": LEVELS[:4]},
    {"value": "claude-haiku-4-5", "resolvedModel": "claude-haiku-4-5", "displayName": "Haiku 4.5"},
]
NO_EFFORT = {"claude-haiku-4-5"}

state = {"model": "opus", "effort": None, "messages": 0}


def arg(flag):
    return sys.argv[sys.argv.index(flag) + 1] if flag in sys.argv else None


def system_prompt():
    """--system-prompt, or the prompt of the agent --agent names (from --agents)."""
    if arg("--agent") and arg("--agents"):
        return (json.loads(arg("--agents")).get(arg("--agent")) or {}).get("prompt") or ""
    return arg("--system-prompt") or ""


def context_usage():
    used = 24000 + 1500 * state["messages"]
    return {"categories": [{"name": "System prompt", "tokens": 3000, "kind": "used"},
                           {"name": "System tools", "tokens": 17000, "kind": "used"},
                           {"name": "Messages", "tokens": used - 20000, "kind": "used"},
                           {"name": "Free space", "tokens": 200000 - used, "kind": "free"}],
            "totalTokens": used, "maxTokens": 200000, "percentage": round(100 * used / 200000)}


def control_reply(req):
    sub = req.get("subtype")
    if sub == "initialize":
        return {"commands": COMMANDS, "models": MODELS, "current_permission_mode": start_mode()}
    if sub == "set_model":
        state["model"] = req.get("model") or "opus"
        if state["model"] in NO_EFFORT:
            state["effort"] = None
    if sub == "apply_flag_settings" and "effortLevel" in (req.get("settings") or {}):
        state["effort"] = req["settings"]["effortLevel"]
    if sub == "get_settings":
        return {"applied": {"model": state["model"], "effort": state["effort"]}}
    if sub == "get_context_usage":
        return context_usage()
    return {}

log = open(os.environ.get("FAKE_CLAUDE_LOG", os.devnull), "a")


def start_mode():
    a = sys.argv
    m = a[a.index("--permission-mode") + 1] if "--permission-mode" in a else "default"
    return "default" if m == "manual" else m


def record(line):
    log.write(line.rstrip("\n") + "\n")
    log.flush()


OUT_LOCK = threading.Lock()  # background tasks write from timer threads


def out(obj, delay=0.005):
    with OUT_LOCK:
        print(json.dumps(obj), flush=True)
    time.sleep(delay)


def stream(ev):
    out({"type": "stream_event", "event": ev, "parent_tool_use_id": None, "session_id": SESSION})


def turn(n):
    mid = f"msg{n}a"
    stream({"type": "message_start", "message": {"id": mid}})
    stream({"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": ""}})
    stream({"type": "content_block_delta", "index": 0,
            "delta": {"type": "thinking_delta", "thinking": "The user wants a file listing."}})
    stream({"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": ""}})
    for chunk in ["## Plan\n", "I'll **list** the files ", "with `ls`:\n", "```bash\nls -la\n```\n"]:
        stream({"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": chunk}})
    tid = f"tool{n}"
    stream({"type": "content_block_start", "index": 2,
            "content_block": {"type": "tool_use", "id": tid, "name": "Bash", "input": {}}})
    for c in ['{"comm', 'and": "ls -la"}']:
        stream({"type": "content_block_delta", "index": 2, "delta": {"type": "input_json_delta", "partial_json": c}})
    out({"type": "assistant", "parent_tool_use_id": None, "session_id": SESSION,
         "message": {"id": mid, "model": "fake", "role": "assistant",
                     "content": [{"type": "tool_use", "id": tid, "name": "Bash", "input": {"command": "ls -la"}}]}})
    out({"type": "control_request", "request_id": f"perm{n}",
         "request": {"subtype": "can_use_tool", "tool_name": "Bash", "input": {"command": "ls -la"},
                     "tool_use_id": tid}})
    while True:  # wait for the permission answer (ignore anything else)
        line = sys.stdin.readline()
        if not line:
            sys.exit(0)
        record(line)
        msg = json.loads(line)
        if msg.get("type") == "control_response" and msg["response"].get("request_id") == f"perm{n}":
            break
    allowed = msg["response"]["response"].get("behavior") == "allow"
    out({"type": "user", "session_id": SESSION, "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": tid, "is_error": not allowed,
         "content": "total 8\nbaude.py" if allowed else "Permission denied by user"}]}})
    stream({"type": "message_start", "message": {"id": f"msg{n}b"}})
    stream({"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}})
    stream({"type": "content_block_delta", "index": 0,
            "delta": {"type": "text_delta", "text": "Done, one file." if allowed else "OK, skipped it."}})
    out({"type": "result", "subtype": "success", "is_error": False, "session_id": SESSION,
         "total_cost_usd": 0.0125, "num_turns": 2, "duration_ms": 1500,
         "usage": {"input_tokens": 1000, "output_tokens": 50}})


TASKS = {}  # task_id -> {"timer": Timer, "tool_use_id": ...}


def system(subtype, **kw):
    out({"type": "system", "subtype": subtype, "session_id": SESSION, **kw})


def say(text, mid):
    """A complete streamed assistant text message."""
    stream({"type": "message_start", "message": {"id": mid}})
    stream({"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}})
    stream({"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}})
    out({"type": "assistant", "parent_tool_use_id": None, "session_id": SESSION,
         "message": {"id": mid, "model": "fake", "role": "assistant", "content": [{"type": "text", "text": text}]}})


def result(text):
    out({"type": "result", "subtype": "success", "is_error": False, "session_id": SESSION, "result": text,
         "total_cost_usd": 0.001, "num_turns": 1, "duration_ms": 300, "usage": {"input_tokens": 10, "output_tokens": 5}})


def finish_task(tid, status="completed", summary=None):
    t = TASKS.pop(tid, None)
    if t is None:
        return
    system("task_updated", task_id=tid, patch={"status": status})
    system("task_notification", task_id=tid, tool_use_id=t["tool_use_id"], status=status,
           output_file=t["output_file"], summary=summary or t["summary"], usage=t.get("usage"))
    system("background_tasks_changed", tasks=[{"task_id": k, "task_type": v["type"]} for k, v in TASKS.items()])
    if status == "completed":  # like the real CLI: a new turn starts by itself
        system("status", status="requesting")
        say(t["followup"], f"follow-{tid}")
        result(t["followup"])


def background_command(n, delay):
    """Bash with run_in_background: the result only says it started; it finishes later."""
    tid, tool = f"bg{n}", f"toolu_bg{n}"
    out_file = os.path.join(tempfile.gettempdir(), f"fake-claude-{os.getpid()}-{tid}.output")
    with open(out_file, "w") as f:
        f.write("building…\n")
    cmd = {"command": "sleep 2; make build", "description": "Build in the background", "run_in_background": True}
    out({"type": "assistant", "parent_tool_use_id": None, "session_id": SESSION, "message": {
        "id": f"m{tid}", "model": "fake", "role": "assistant",
        "content": [{"type": "tool_use", "id": tool, "name": "Bash", "input": cmd}]}})
    TASKS[tid] = {"tool_use_id": tool, "type": "local_bash", "output_file": out_file,
                  "summary": 'Background command "Build in the background" completed (exit code 0)',
                  "followup": "The build finished: **BUILD OK**."}
    system("task_started", task_id=tid, tool_use_id=tool, description=cmd["description"],
           task_type="local_bash", is_backgrounded=True)
    system("background_tasks_changed", tasks=[{"task_id": tid, "task_type": "local_bash"}])
    out({"type": "user", "session_id": SESSION, "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": tool, "content":
         f"Command running in background with ID: {tid}. Output is being written to: {out_file}"}]}})
    say("Started the build in the background.", f"s{tid}")
    result("started")

    def done():
        with open(out_file, "a") as f:
            f.write("compiling 12 files\nBUILD OK\n")
        finish_task(tid)
    if delay is not None:
        TASKS[tid]["timer"] = threading.Timer(delay, done)
        TASKS[tid]["timer"].start()


def subagent(n, delay):
    """An Agent call: runs as a background task, forwards its own messages, then reports."""
    tid, tool = f"ag{n}", f"toolu_ag{n}"
    inp = {"description": "Survey the tests", "subagent_type": "Explore",
           "prompt": "Count the test functions in `tests/test_baude.py`."}
    out({"type": "assistant", "parent_tool_use_id": None, "session_id": SESSION, "message": {
        "id": f"m{tid}", "model": "fake", "role": "assistant",
        "content": [{"type": "tool_use", "id": tool, "name": "Agent", "input": inp}]}})
    TASKS[tid] = {"tool_use_id": tool, "type": "local_agent", "output_file": "/dev/null",
                  "summary": "There are **28** test functions.", "followup": "The subagent counted 28 tests.",
                  "usage": {"total_tokens": 18000, "tool_uses": 1, "duration_ms": 900}}
    system("task_started", task_id=tid, tool_use_id=tool, description=inp["description"],
           subagent_type="Explore", task_type="local_agent", is_backgrounded=True, prompt=inp["prompt"])
    system("background_tasks_changed", tasks=[{"task_id": tid, "task_type": "local_agent"}])
    out({"type": "user", "session_id": SESSION, "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": tool, "content": [
            {"type": "text", "text": "Async agent launched successfully."}]}]}})
    say("A subagent is surveying the tests.", f"s{tid}")
    result("launched")

    def work():
        sub = lambda content: out({"type": "assistant", "parent_tool_use_id": tool, "session_id": SESSION,  # noqa: E731
                                   "message": {"id": f"sub{tid}{len(content)}", "model": "fake", "role": "assistant",
                                               "content": content}})
        sub([{"type": "tool_use", "id": f"{tool}_g", "name": "Grep",
              "input": {"pattern": "^def test_", "path": "tests/test_baude.py", "output_mode": "count"}}])
        system("task_progress", task_id=tid, tool_use_id=tool, description="Running Grep",
               subagent_type="Explore", usage={"total_tokens": 9000, "tool_uses": 1, "duration_ms": 400},
               last_tool_name="Grep")
        out({"type": "user", "parent_tool_use_id": tool, "session_id": SESSION, "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": f"{tool}_g", "content": "28"}]}})
        sub([{"type": "text", "text": "There are **28** test functions."}])
        finish_task(tid)
    if delay is not None:
        TASKS[tid]["timer"] = threading.Timer(delay, work)
        TASKS[tid]["timer"].start()


def nested_subagent(n, hold):
    """An agent that starts its own agent: the inner one's messages carry the inner Agent call's id,
    its task_started arrives like any other. HOLD leaves the outer one running once the inner is done."""
    tid, tool = f"ag{n}", f"toolu_ag{n}"
    itid, itool = f"ag{n}i", f"toolu_ag{n}_inner"
    inp = {"description": "Audit the app", "subagent_type": "general-purpose",
           "prompt": "Audit `baude.py` for dead code; have a helper check the tests."}
    out({"type": "assistant", "parent_tool_use_id": None, "session_id": SESSION, "message": {
        "id": f"m{tid}", "model": "fake", "role": "assistant",
        "content": [{"type": "tool_use", "id": tool, "name": "Agent", "input": inp}]}})
    TASKS[tid] = {"tool_use_id": tool, "type": "local_agent", "output_file": "/dev/null",
                  "summary": "No dead code; the helper counted **28** tests.",
                  "followup": "The audit found no dead code.",
                  "usage": {"total_tokens": 41000, "tool_uses": 3, "duration_ms": 2100}}
    system("task_started", task_id=tid, tool_use_id=tool, description=inp["description"],
           subagent_type="general-purpose", task_type="local_agent", is_backgrounded=True, prompt=inp["prompt"])
    system("background_tasks_changed", tasks=[{"task_id": tid, "task_type": "local_agent"}])
    out({"type": "user", "session_id": SESSION, "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": tool, "content": [
            {"type": "text", "text": "Async agent launched successfully."}]}]}})
    say("An agent is auditing the app.", f"s{tid}")
    result("launched")

    def said(parent, content, key):
        out({"type": "assistant", "parent_tool_use_id": parent, "session_id": SESSION,
             "message": {"id": f"sub{key}", "model": "fake", "role": "assistant", "content": content}})

    def returned(parent, tool_id, content):
        out({"type": "user", "parent_tool_use_id": parent, "session_id": SESSION, "message": {"role": "user",
             "content": [{"type": "tool_result", "tool_use_id": tool_id, "content": content}]}})

    def work():
        said(tool, [{"type": "tool_use", "id": f"{tool}_r", "name": "Read", "input": {"file_path": "baude.py"}}], "o1")
        returned(tool, f"{tool}_r", "#!/usr/bin/env python3\n\"\"\"baude\"\"\"\n")
        iinp = {"description": "Check the tests", "subagent_type": "Explore",
                "prompt": "Count the test functions in `tests/test_baude.py`."}
        said(tool, [{"type": "tool_use", "id": itool, "name": "Agent", "input": iinp}], "o2")
        TASKS[itid] = {"tool_use_id": itool, "type": "local_agent", "output_file": "/dev/null",
                       "summary": "There are **28** test functions.", "followup": None,
                       "usage": {"total_tokens": 12000, "tool_uses": 1, "duration_ms": 700}}
        system("task_started", task_id=itid, tool_use_id=itool, description=iinp["description"],
               subagent_type="Explore", task_type="local_agent", prompt=iinp["prompt"])
        said(itool, [{"type": "tool_use", "id": f"{itool}_g", "name": "Grep",
                      "input": {"pattern": "^def test_", "path": "tests/test_baude.py", "output_mode": "count"}}], "i1")
        returned(itool, f"{itool}_g", "28")
        said(itool, [{"type": "text", "text": "There are **28** test functions."}], "i2")
        t = TASKS.pop(itid)
        system("task_notification", task_id=itid, tool_use_id=itool, status="completed",
               output_file="/dev/null", summary=t["summary"], usage=t["usage"])
        returned(tool, itool, [{"type": "text", "text": t["summary"]}])
        said(tool, [{"type": "text", "text": "Nothing unused, and the helper counted **28** tests."}], "o3")
        system("task_progress", task_id=tid, tool_use_id=tool, description="Writing the report",
               subagent_type="general-purpose", usage={"total_tokens": 38000, "tool_uses": 3, "duration_ms": 1900})
        if not hold:
            finish_task(tid)
    TASKS[tid]["timer"] = threading.Timer(0.3, work)
    TASKS[tid]["timer"].start()


READER = {"proc": None, "n": 0}
# what the fake reader decides for names it finds in a part's replies (only where a reply writes the name)
READER_SCRIPT = [
    ("the job runner", {"verdict": "link", "path": "svc/app", "role": "folder", "kind": "service",
                        "note": "runs queued jobs"}),
    ("routes.py", {"verdict": "link", "path": "svc/app/routes.py", "role": "file", "kind": "file"}),
    ("Settings", {"verdict": "link", "path": "svc/app/settings.py", "line": 1, "kind": "class"}),
    ("SVC_NEW_FLAG", {"lookup": True, "verdict": "not_in_code", "why": "proposed", "note": "suggested above"}),
    ("Karpenter", {"verdict": "not_in_code", "why": "external", "note": "Kubernetes node autoscaler"}),
    ("ghost_helper", {"verdict": "link", "path": "svc/app/loop.py", "line": 3, "kind": "function"}),
    ("graph", {"verdict": "plain"}),
    ("max_step", {"verdict": "link", "path": "svc/zz/prefs.py", "line": 2, "kind": "field"}),
    ("unit", {"lookup": True, "verdict": "link", "path": "svc/zz/prefs.py", "line": 3, "kind": "field"}),
    ("nodes", {"verdict": "not_in_code", "why": "elsewhere", "note": "another service's", "search_after": True}),
    ("zz", {"verdict": "plain"}),
    ("preview", {"verdict": "link", "path": "svc/zz/db.py", "line": 4, "role": "column", "kind": "field"}),
    ("svc.helpers.trim", {"verdict": "plain"}),
    # (Kubernetes: a prose name placed through the kube tool; a values key placed where the reply says, not the rules')
    ("runner ingress", {"kube": {"name": "runner-ingress"}, "verdict": "link", "kind": "k8s", "role": "Ingress",
                        "note": "the runner's Ingress"}),
    ("prod overlay", {"verdict": "link", "path": "deploy/k8s/overlays/prod", "role": "folder", "kind": "config",
                      "note": "the kustomize overlay for prod"}),
    ("image.tag", {"lookup": True, "verdict": "link", "path": "charts/app/values-production.yaml", "line": 2,
                   "role": "value", "kind": "config", "note": "prod's image tag"}),
]


def mcp(method, params=None):
    """A call to Signal's tools, through the relay --mcp-config names (as Claude Code would)."""
    if READER["proc"] is None:
        with open(arg("--mcp-config")) as f:
            server = json.load(f)["mcpServers"]["signal"]
        READER["proc"] = subprocess.Popen([server["command"], *server["args"]], stdin=subprocess.PIPE,
                                          stdout=subprocess.PIPE, text=True)
        READER["id"] = 0
        mcp("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}})
    READER["id"] += 1
    p = READER["proc"]
    p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": READER["id"], "method": method, "params": params or {}}) + "\n")
    p.stdin.flush()
    msg = json.loads(p.stdout.readline())
    result = msg.get("result") or {}
    if method == "tools/call":
        return json.loads(result["content"][0]["text"])
    return result


def reader(text):
    """Signal's reader: what it was given (logged), the tools it calls, the decisions it makes."""
    READER["n"] += 1
    k = READER["n"]
    earlier = text.split("Decided in earlier replies", 1)[1] if "Decided in earlier replies" in text else ""
    intro = "Signal reader" in text or "Signal reader" in system_prompt()
    record(f"READER part {k} tools={'[Claude →' in text} you={'[you]' in text} intro={intro} "
           f"builtin={arg('--tools')} agent={arg('--agent')} sources={arg('--setting-sources')} "
           f"memory={'off' if os.environ.get('CLAUDE_CODE_DISABLE_AUTO_MEMORY') == '1' else 'on'} "
           f"connectors={os.environ.get('ENABLE_CLAUDEAI_MCP_SERVERS', 'default')} "
           f"saw={'Claude saw it at' in text} rules={'rules found' in text} "
           f"toolsearch={os.environ.get('ENABLE_TOOL_SEARCH', 'default')} "
           f"earlier={json.dumps(re.findall(r'^- `([^`]+)` [(]reply (\d+)[)]: ([^\n]*)', earlier, re.M))}")
    if "HOLD" in text:
        time.sleep(3)
    tools = [t["name"] for t in mcp("tools/list").get("tools", [])]
    record(f"READER tools {tools}")
    replies = dict(re.findall(r"\[reply (\d+)\]\n(.*?)(?=\n\[(?:reply \d+|you|Claude)|\nThe backticked names|\nBaude's rules found|"
                              r"\nDecide every name|\Z)", text, re.S))
    kubeconfig = (os.environ.get("KUBECONFIG") or "~/.kube/config").split(os.pathsep)[0]
    for i, (tool, inp) in enumerate((("Bash", {"command": "git log --oneline -3"}), ("Bash", {"command": "rm -rf svc"}),
                                     ("Read", {"file_path": kubeconfig}))):  # (a kubeconfig: never)
        out({"type": "control_request", "request_id": f"ref-perm-{k}-{i}",
             "request": {"subtype": "can_use_tool", "tool_name": tool, "input": inp,
                         "tool_use_id": f"ref-tool-{k}-{i}"}})
    found = [(int(n), name, d) for n, body in replies.items() for name, d in READER_SCRIPT
             if re.search(r"(?<![\w])" + re.escape(name) + r"(?![\w])", body, re.I)]
    known = mcp("tools/call", {"name": "known", "arguments": {"names": [f[1] for f in found], "reply": 1}})
    record(f"READER known {json.dumps(known)[:2000]}")
    decisions, later = [], []
    for n, name, d in found:
        if d.get("lookup"):
            record(f"READER lookup {name} {json.dumps(mcp('tools/call', {'name': 'lookup', 'arguments': {'name': name}}))}")
        place = {}
        if d.get("kube"):  # (the object it means, from Signal's Kubernetes catalogue)
            res = mcp("tools/call", {"name": "kube", "arguments": d["kube"]})
            record(f"READER kube {json.dumps(res)}")
            if res.get("objects"):
                path, _, line = res["objects"][0]["place"].rpartition(":")
                place = {"path": path, "line": int(line)}
        decisions.append({"reply": n, "name": name, **place,
                          **{k2: v for k2, v in d.items() if k2 not in ("lookup", "search_after", "kube")}})
        if d.get("search_after"):
            later.append((n, name, d))
    res = mcp("tools/call", {"name": "decide", "arguments": {"decisions": decisions}})
    record(f"READER decide {json.dumps(res)}")
    for n, name, d in later:  # (rejected without a search: search, then decide again)
        mcp("tools/call", {"name": "lookup", "arguments": {"name": name}})
        res = mcp("tools/call", {"name": "decide", "arguments": {"decisions": [
            {"reply": n, "name": name, **{k2: v for k2, v in d.items() if k2 not in ("lookup", "search_after")}}]}})
        record(f"READER decide again {json.dumps(res)}")
    answer = f"Part {k} decided."
    say(answer, f"reader{k}")
    out({"type": "result", "subtype": "success", "is_error": False, "session_id": SESSION, "result": answer,
         "total_cost_usd": 0.002 * k, "num_turns": 3, "duration_ms": 900, "usage": {"input_tokens": 50, "output_tokens": 40}})


def main():
    record(f"START cwd={os.getcwd()} argv={json.dumps(sys.argv[1:])}")
    state["model"] = arg("--model") or "opus"
    state["effort"] = arg("--effort") or ("medium" if state["model"] not in NO_EFFORT else None)
    turns = 0
    for line in sys.stdin:
        record(line)
        msg = json.loads(line)
        if msg.get("type") == "control_request":
            sub = msg["request"].get("subtype")
            resp = control_reply(msg["request"])
            out({"type": "control_response",
                 "response": {"subtype": "success", "request_id": msg["request_id"], "response": resp}})
            if sub == "set_permission_mode":
                out({"type": "system", "subtype": "status", "status": None,
                     "permissionMode": msg["request"]["mode"], "session_id": SESSION})
            if sub == "stop_task" and msg["request"].get("task_id") in TASKS:
                t = TASKS[msg["request"]["task_id"]]
                if t.get("timer"):
                    t["timer"].cancel()
                finish_task(msg["request"]["task_id"], "killed", "Stopped by the user")
            if sub == "rename_session":
                system("session_title_changed", title=msg["request"].get("title"))
            if sub == "initialize":
                out({"type": "system", "subtype": "init", "session_id": SESSION, "cwd": os.getcwd(),
                     "model": "fake-model", "tools": ["Bash", "Read"]})
        elif msg.get("type") == "user":
            turns += 1
            state["messages"] += 1
            text = msg["message"]["content"] if isinstance(msg["message"]["content"], str) else ""
            # "BACKGROUND" / "SUBAGENT" scripts; "...HOLD" keeps the task running until stopped
            delay = None if "HOLD" in text else 0.6
            if "You are Baude's Signal reader" in text or re.match(r"Part \d+: replies", text) or \
                    "Signal reader" in system_prompt():
                reader(text)
                continue
            if "--no-session-persistence" in sys.argv:  # a /btw side question: a short answer, no tools
                question = text.split("\n\n")[-1]
                say(f"Side answer ({'forked from ' + arg('--resume') if '--fork-session' in sys.argv else 'no context'})"
                    f" to: {question}", f"btw{turns}")
                result("side answer")
                continue
            if "BACKGROUND" in text:
                background_command(turns, delay)
            elif "NESTED" in text:
                nested_subagent(turns, "HOLD" in text)
            elif "SUBAGENT" in text:
                subagent(turns, delay)
            else:
                turn(turns)


def auth(args):
    """`claude auth login|status|logout`, as claude 2.1.309's prints them (no terminal needed): login says where to
    sign in (the address as a terminal link), then reads a pasted code#state line: "good#..." signs in, anything else
    fails; FAKE_AUTH=wait waits to be stopped. status --json says who's signed in (no one after logout)."""
    signed = os.path.join(tempfile.gettempdir(), f"fake-claude-auth-{os.getuid()}")
    if args[:1] == ["login"]:
        record(f"AUTH LOGIN {json.dumps(args[1:])}")
        url = "https://claude.ai/oauth/authorize?code=true&state=abc"
        sys.stdout.write("Opening browser to sign in\u2026\n")
        sys.stdout.write(f"If the browser didn't open, visit: \x1b]8;;{url}\x07{url}\x1b]8;;\x07\n")
        sys.stdout.write("Paste code here if prompted > ")
        sys.stdout.flush()
        if os.environ.get("FAKE_AUTH") == "wait":
            time.sleep(60)
        line = sys.stdin.readline().strip()
        if line.startswith("good#"):
            open(signed, "w").close()
            sys.stdout.write("Login successful.\n")
            sys.exit(0)
        sys.stderr.write("Login failed: Invalid code. Please make sure the full code was copied\n")
        sys.exit(1)
    if args[:1] == ["status"]:
        print(json.dumps({"loggedIn": os.path.exists(signed), "authMethod": "claude.ai", "email": "fake@example.com",
                          "orgName": "Fake Org", "subscriptionType": "team"}))
        sys.exit(0)
    if args[:1] == ["logout"]:
        record("AUTH LOGOUT")
        if os.path.exists(signed):
            os.remove(signed)
        print("Successfully logged out from your Anthropic account.")
        sys.exit(0)
    sys.exit(2)


if __name__ == "__main__":
    if sys.argv[1:2] == ["auth"]:
        auth(sys.argv[2:])
    main()
