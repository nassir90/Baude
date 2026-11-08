#!/usr/bin/env python3
"""Record's stand-in for `claude -p ... --output-format stream-json`: streams a canned reply, word by word,
that echoes what the prompt gave it (glossary size, how many messages it saw)."""
import json, os, re, sys, time

prompt = sys.stdin.read()
if os.environ.get("FAKE_RECORD_LOG"):  # (what it was asked, for the tests to read)
    with open(os.environ["FAKE_RECORD_LOG"], "a") as f:
        f.write(json.dumps({"argv": sys.argv[1:], "prompt": prompt}) + "\n")
terms = re.findall(r"^- ([^:\n]+):", prompt, re.M)
seen = len(re.findall(r"^(User|Record): ", prompt, re.M))
print(json.dumps({"type": "system", "subtype": "init", "model": "claude-haiku-5-5"}), flush=True)
reply = (f"I think this is saying the thing works a bit like **{terms[0] if terms else 'a recipe'}**: "
         f"one step hands its result to the next. I can see {seen} messages and {len(terms)} glossary terms. "
         "My guess is the rest is about ordering.\nUnknown: stream protocol; idempotency")
for w in re.split(r"(\s+)", reply):
    print(json.dumps({"type": "stream_event", "event": {"type": "content_block_delta",
                      "delta": {"type": "text_delta", "text": w}}}), flush=True)
    time.sleep(0.01)
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": reply}), flush=True)
