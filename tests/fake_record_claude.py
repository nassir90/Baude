#!/usr/bin/env python3
"""Record's stand-in for claude. Asked once (`claude -p ... --output-format stream-json`, the prompt on stdin): streams a
canned reply, word by word, that echoes what the prompt gave it (glossary size, how many messages it saw).

As a session (`--input-format stream-json`, as Record's agent runs it through baudelib's ClaudeProc): answers each
message in turn. A message with a passage selected on a page and asking to fill it in proposes the change through the
agent's own MCP server (its --mcp-config, run as Claude Code runs it: propose_edit), filling it from the page
referenced with it; one asking to read /a/path asks permission to (can_use_tool, as Claude Code asks for a read
outside its folders) and says what it was told; one with lines CALL <tool> <json arguments>, or any while
FAKE_META_SCRIPT's file has turns in it, calls those tools of Meta's, in order, and replies with what's written after
them (or the turn's reply); any other is a canned reply that says what was referenced."""
import json, os, re, subprocess, sys, time


def log(prompt):
    if os.environ.get("FAKE_RECORD_LOG"):  # (what it was asked, for the tests to read)
        with open(os.environ["FAKE_RECORD_LOG"], "a") as f:
            f.write(json.dumps({"argv": sys.argv[1:], "prompt": prompt}) + "\n")


def out(msg):
    print(json.dumps(msg), flush=True)


def stream(reply):
    out({"type": "stream_event", "event": {"type": "message_start", "message": {"role": "assistant"}}})
    for w in re.split(r"(\s+)", reply):
        out({"type": "stream_event", "event": {"type": "content_block_delta",
                                               "delta": {"type": "text_delta", "text": w}}})
        time.sleep(0.01)
    out({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": reply}]}})


def once():
    prompt = sys.stdin.read()
    log(prompt)
    terms = re.findall(r"^- ([^:\n]+):", prompt, re.M)
    seen = len(re.findall(r"^(User|Record): ", prompt, re.M))
    out({"type": "system", "subtype": "init", "model": "claude-haiku-5-5"})
    reply = (f"I think this is saying the thing works a bit like **{terms[0] if terms else 'a recipe'}**: "
             f"one step hands its result to the next. I can see {seen} messages and {len(terms)} glossary terms. "
             "My guess is the rest is about ordering.\nUnknown: stream protocol; idempotency")
    for w in re.split(r"(\s+)", reply):
        out({"type": "stream_event", "event": {"type": "content_block_delta",
                                               "delta": {"type": "text_delta", "text": w}}})
        time.sleep(0.01)
    out({"type": "result", "subtype": "success", "is_error": False, "result": reply})


class Mcp:
    """The agent's MCP server, from --mcp-config, started and spoken to as Claude Code does (stdio JSON-RPC)."""

    def __init__(self, name):
        arg = sys.argv[sys.argv.index("--mcp-config") + 1]
        cfg = json.loads(arg) if arg.lstrip().startswith("{") else json.load(open(arg))
        server = cfg["mcpServers"][name]
        self.p = subprocess.Popen([server["command"], *server.get("args", [])], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, text=True)
        self.ids = 0
        self.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "fake"}})
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        self.tools = [t["name"] for t in self.rpc("tools/list", {})["tools"]]

    def rpc(self, method, params):
        self.ids += 1
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self.ids, "method": method, "params": params}) + "\n")
        self.p.stdin.flush()
        return json.loads(self.p.stdout.readline())["result"]


def fill(text, selection):
    """A passage selected on a page, filled in from the page referenced with it: (the edit's input, the reply: none)."""
    page, passage = selection
    refs = re.findall(r'<page notebook="[^"]*" name="([^"]+)" file="[^"]*">\n(.*?)\n</page>', text, re.S)
    facts = [re.sub(r"^- ", "", line).rstrip(".") for _name, body in refs for line in body.splitlines()
             if line.startswith("- ")]
    facts = [f[0].lower() + f[1:] for f in facts]
    new = ("It decides bed by bed: " + "; ".join(facts) + ".") if facts else "TODO: how it decides."
    return {"page": page, "old_text": passage, "new_text": new}, ""  # (the edit says itself: nothing more said)


def scripted():
    """The next turn of FAKE_META_SCRIPT's, if it names a file of them (a JSON list of {calls: [[tool, {arguments}]],
    reply}), taken from it: its calls, its reply kept as scripted.reply; else none."""
    scripted.reply, path = None, os.environ.get("FAKE_META_SCRIPT")
    if not path or not os.path.exists(path):
        return []
    with open(path) as f:
        turns = json.load(f)
    if not turns:
        return []
    with open(path, "w") as f:
        json.dump(turns[1:], f)
    scripted.reply = turns[0].get("reply") or ""
    return [(tool, json.dumps(args)) for tool, args in turns[0].get("calls") or ()]


def session():
    out({"type": "system", "subtype": "init", "model": "claude-haiku-5-5", "tools": ["Read", "Grep", "Glob"]})
    mcp = None
    for line in sys.stdin:
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if msg.get("type") == "control_request":
            out({"type": "control_response", "response": {"subtype": "success", "request_id": msg["request_id"],
                                                          "response": {"commands": [], "models": []}}})
            continue
        if msg.get("type") != "user":
            continue
        text = msg["message"]["content"]
        log(text)
        sel = re.search(r'<selection page="([^"]+)" file="[^"]*">\n(.*?)\n</selection>', text, re.S)
        asks = text.rstrip().rpartition("\n")[2].lower()
        if (path := re.search(r"\bread (/\S+)", asks)) is not None:
            out({"type": "control_request", "request_id": "ask1", "request": {
                "subtype": "can_use_tool", "tool_name": "Read", "input": {"file_path": path.group(1)}}})
            for answer in sys.stdin:  # (as Claude Code waits for it)
                got = json.loads(answer)
                if got.get("type") == "control_response" and got["response"]["request_id"] == "ask1":
                    break
            told = got["response"].get("response") or {}
            reply = f"I was {'let' if told.get('behavior') == 'allow' else 'not let'} read {path.group(1)}."
        elif calls := scripted() or re.findall(r"^CALL (\w+) (\{.*\})$", text, re.M):
            mcp = mcp or Mcp("meta")
            for n, (tool, args) in enumerate(calls):
                out({"type": "assistant", "message": {"role": "assistant", "content": [
                    {"type": "tool_use", "id": f"c{n}", "name": f"mcp__meta__{tool}", "input": json.loads(args)}]}})
                res = mcp.rpc("tools/call", {"name": tool, "arguments": json.loads(args)})
                out({"type": "user", "message": {"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": f"c{n}", "content": res["content"],
                     "is_error": res["isError"]}]}})
            reply = scripted.reply if scripted.reply is not None else \
                re.split(r"^CALL .*$", text, flags=re.M)[-1].strip()  # (what's written after the last call)
        elif sel and re.search(r"\b(fill|write|complete)\b", asks):
            change, reply = fill(text, (sel.group(1), sel.group(2)))
            mcp = mcp or Mcp("meta")
            out({"type": "assistant", "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": "t1", "name": "mcp__meta__propose_edit", "input": change}]}})
            res = mcp.rpc("tools/call", {"name": "propose_edit", "arguments": change})
            out({"type": "user", "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": res["content"], "is_error": res["isError"]}]}})
        else:
            refs = re.findall(r'^<(page|file|notebook|chat|folder|ref) [^>]*?(?:name|title)="([^"]+)"', text, re.M)
            said = ", ".join(f"the {kind} {name}" for kind, name in refs) or "nothing referenced"
            seen = "a passage" if "[passage]" in text else "no passage"
            reply = (f"I can see {said}, and {seen}. It reads as one step handing its result to the next." +
                     ("\nUnknown: stream protocol; idempotency" if "[passage]" in text else ""))
        stream(reply)
        out({"type": "result", "subtype": "success", "is_error": False, "result": reply})


if "--input-format" in sys.argv:
    session()
else:
    once()
