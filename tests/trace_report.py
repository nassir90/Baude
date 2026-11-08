"""What Signal's reader spent, per part and per API call, from tests/trace_claude.py's records.

    tests/trace_report.py TRACE_DIR

Per part (one user message to the reader's session and everything up to its result): wall time, API calls, time
before each call's response starts (the request, and the model reading the context), time spent generating (and
to the first token), time waiting on tools (Baude's MCP tools run on Signal's worker
thread, behind the rules' lookups), the context's size at the last call, cache reads and writes, output tokens,
thinking and tool-input characters, and the cost at Sonnet's API prices beside the one Claude Code reports. Then
totals, cost by kind of token, and each tool's calls, waits and result sizes.
"""
import collections, glob, json, re, sys

PRICE = {"in": 2.0, "cache_write": 4.0, "cache_read": 0.10, "out": 10.0}  # Sonnet 5.5, $ per million tokens:
# fitted to the result messages' costs (Claude Code writes its cache for an hour, at twice the input price)


def load(path):
    recs = []
    for line in open(path):
        r = json.loads(line)
        if r["d"] in ("in", "out"):
            try:
                r["m"] = json.loads(r["l"])
            except ValueError:
                continue
        recs.append(r)
    return recs


def parts_of(recs):
    """Each user message sent (a part) and everything until its result."""
    parts, cur = [], None
    for r in recs:
        m = r.get("m") or {}
        if r["d"] == "in" and m.get("type") == "user":
            cur = {"t0": r["t"], "chars": len(m["message"]["content"]) if isinstance(m["message"]["content"], str) else 0,
                   "head": (m["message"]["content"][:60] if isinstance(m["message"]["content"], str) else ""),
                   "calls": [], "tools": [], "result": None}
            parts.append(cur)
            open_tools = {}
            call = None
            last = r["t"]  # (the last thing sent or received: the next call's request starts about then)
            continue
        if cur is None or r["d"] != "out":
            continue
        t = m.get("type")
        if t == "stream_event" and not m.get("parent_tool_use_id"):
            ev = m["event"]
            et = ev.get("type")
            if et == "message_start":
                u = ev["message"].get("usage", {})
                call = {"t0": r["t"], "first": None, "wait": r["t"] - last, "in": u.get("input_tokens", 0),
                        "cw": u.get("cache_creation_input_tokens", 0), "cr": u.get("cache_read_input_tokens", 0),
                        "out": 0, "think": 0, "text": 0, "tool_json": 0, "tools": []}
                cur["calls"].append(call)
            elif et == "content_block_start" and call is not None:
                b = ev.get("content_block", {})
                if b.get("type") == "tool_use":
                    call["tools"].append(b.get("name"))
            elif et == "content_block_delta" and call is not None:
                d = ev.get("delta", {})
                call["first"] = call["first"] or r["t"]
                if d.get("type") == "thinking_delta":
                    call["think"] += len(d.get("thinking", ""))
                elif d.get("type") == "text_delta":
                    call["text"] += len(d.get("text", ""))
                elif d.get("type") == "input_json_delta":
                    call["tool_json"] += len(d.get("partial_json", ""))
            elif et == "message_delta" and call is not None:
                call["out"] = ev.get("usage", {}).get("output_tokens", call["out"])
            elif et == "message_stop" and call is not None:
                call["t1"] = r["t"]
                last = r["t"]
        elif t == "assistant" and not m.get("parent_tool_use_id"):
            for c in m["message"].get("content", []):
                if c.get("type") == "tool_use":
                    open_tools[c["id"]] = {"name": c["name"], "t": r["t"], "input": json.dumps(c.get("input"))}
        elif t == "user" and not m.get("parent_tool_use_id"):
            for c in (m.get("message", {}).get("content") or []):
                if isinstance(c, dict) and c.get("type") == "tool_result" and c.get("tool_use_id") in open_tools:
                    o = open_tools.pop(c["tool_use_id"])
                    body = c.get("content")
                    size = len(json.dumps(body)) if not isinstance(body, str) else len(body)
                    last = r["t"]
                    cur["tools"].append({"name": o["name"], "wait": r["t"] - o["t"], "in_chars": len(o["input"]),
                                         "out_chars": size, "input": o["input"][:120]})
        elif t == "result":
            cur["result"] = m
            cur["t1"] = r["t"]
    return parts


def money(c):
    return (c["in"] * PRICE["in"] + c["cw"] * PRICE["cache_write"] + c["cr"] * PRICE["cache_read"] +
            c["out"] * PRICE["out"]) / 1e6


def main():
    def started(path):
        with open(path) as f:
            return json.loads(f.readline() or '{"t": 0}')["t"]
    files = sorted(glob.glob(sys.argv[1] + "/claude-*.jsonl"), key=started)  # (a session per part: in order)
    n = 0
    tot = collections.Counter()
    tool_tot = collections.defaultdict(lambda: collections.Counter())
    rows = []
    for f in files:
        recs = load(f)
        spent = 0.0  # (a result's total_cost_usd is the session's so far)
        argv = next((r["l"] for r in recs if r["d"] == "argv"), "")
        if "--mcp-config" not in argv:
            continue
        for p in parts_of(recs):
            n += 1
            k = n
            res = p["result"] or {}
            calls = p["calls"]
            part_cost = (res.get("total_cost_usd") or spent) - spent
            spent = res.get("total_cost_usd") or spent
            gen = sum(c.get("t1", c["t0"]) - c["t0"] for c in calls)
            ttft = sum((c["first"] or c["t0"]) - c["t0"] for c in calls)
            tool_wait = sum(t["wait"] for t in p["tools"])
            wall = (p.get("t1") or p["t0"]) - p["t0"]
            cost = sum(money(c) for c in calls)
            row = {"k": k, "wall": wall, "calls": len(calls), "gen": gen, "ttft": ttft, "tool_wait": tool_wait,
                   "api_wait": sum(c["wait"] for c in calls),
                   "ctx": calls[-1]["cr"] + calls[-1]["cw"] + calls[-1]["in"] if calls else 0,
                   "cr": sum(c["cr"] for c in calls), "cw": sum(c["cw"] for c in calls), "in": sum(c["in"] for c in calls),
                   "out": sum(c["out"] for c in calls), "think": sum(c["think"] for c in calls),
                   "text": sum(c["text"] for c in calls), "tool_json": sum(c["tool_json"] for c in calls),
                   "cost_est": cost, "cost": part_cost, "turns": res.get("num_turns"),
                   "thinking_tokens": ((res.get("usage") or {}).get("output_tokens_details") or {}).get("thinking_tokens", 0),
                   "api_ms": res.get("duration_api_ms"), "chars": p["chars"], "tools": collections.Counter(t["name"] for t in p["tools"])}
            rows.append(row)
            for t in p["tools"]:
                tt = tool_tot[t["name"].removeprefix("mcp__signal__")]
                tt["n"] += 1
                tt["wait"] += t["wait"]
                tt["out_chars"] += t["out_chars"]
                tt["in_chars"] += t["in_chars"]
            for c in calls:
                tot["calls"] += 1
                for key in ("in", "cw", "cr", "out", "think", "text", "tool_json"):
                    tot[key] += c[key]
    print(f"{'part':>4} {'wall':>5} {'calls':>5} {'start':>5} {'gen s':>6} {'ttft':>5} {'tools s':>7} {'ctx k':>6} {'read k':>7} "
          f"{'write k':>7} {'out k':>6} {'think':>6} {'json':>6} {'$est':>6} {'$cc':>6}  tools  (think: thinking tokens; "
          f"json: tool-input characters)")
    for r in rows:
        print(f"{r['k']:4} {r['wall']:5.0f} {r['calls']:5} {r['api_wait']:5.0f} {r['gen']:6.0f} {r['ttft']:5.0f} {r['tool_wait']:7.1f} "
              f"{r['ctx']/1e3:6.0f} {r['cr']/1e3:7.0f} {(r['cw']+r['in'])/1e3:7.1f} {r['out']/1e3:6.1f} {r['thinking_tokens']:6} "
              f"{r['tool_json']:6} {r['cost_est']:6.3f} {(r['cost'] or 0):6.3f}  "
              + ", ".join(f"{n.removeprefix('mcp__signal__')} {c}" for n, c in r["tools"].most_common()))
    T = lambda key: sum(r[key] for r in rows)  # noqa: E731
    print(f"\nparts {len(rows)}; wall {T('wall'):.0f} s: waiting for calls to start {T('api_wait'):.0f} s, generating "
          f"{T('gen'):.0f} s (of which to first token {T('ttft'):.0f} s), waiting on tools {T('tool_wait'):.0f} s")
    c = tot
    cost = {k: c[k] * PRICE[p] / 1e6 for k, p in (("in", "in"), ("cw", "cache_write"), ("cr", "cache_read"), ("out", "out"))}
    print(f"API calls {c['calls']}; tokens: input {c['in']}, cache write {c['cw']}, cache read {c['cr']}, output {c['out']}")
    print("cost by kind: " + ", ".join(f"{k} ${v:.2f}" for k, v in cost.items()) + f"; total ${sum(cost.values()):.2f}"
          f" (Claude Code says ${sum((r['cost'] or 0) for r in rows):.2f})")
    print(f"output: thinking {T('thinking_tokens')} tokens; text {c['text']} characters, tool inputs {c['tool_json']}")
    print("\ntools: name, calls, total wait s, mean wait s, result chars, input chars")
    for n, t in sorted(tool_tot.items(), key=lambda x: -x[1]["wait"]):
        print(f"  {n:10} {t['n']:4} {t['wait']:7.1f} {t['wait']/t['n']:6.2f} {t['out_chars']:9} {t['in_chars']:8}")


main()
