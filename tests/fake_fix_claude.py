#!/usr/bin/env python3
"""A stand-in for the claude a fix runs (fixes.run_claude): it changes the code as told (FAKE_FIX_MODE), says what to
show, and streams as claude does. Every run's arguments and prompt are kept in FAKE_FIX_LOG."""
import json
import os
import sys
import time

prompt = sys.stdin.read()
mode = os.environ.get("FAKE_FIX_MODE", "")
with open(os.environ["FAKE_FIX_LOG"], "a") as f:
    f.write(json.dumps({"args": sys.argv[1:], "prompt": prompt, "cwd": os.getcwd(), "pid": os.getpid(),
                        "host_session": os.environ.get("CLAUDECODE")}) + "\n")


def say(**event):
    print(json.dumps(event), flush=True)


say(type="system", subtype="init", session_id="fix-session")
if mode == "fail":
    print("Credit balance is too low", flush=True)
    sys.exit(1)
if mode == "slow":
    time.sleep(120)
if mode == "gone" and "--resume" in sys.argv:  # (a session this machine doesn't have)
    print("No conversation found", flush=True)
    sys.exit(1)
if mode != "nochange":
    with open("widgets.py", encoding="utf-8") as f:
        code = f.read()
    outline = ".composer-box { box-shadow: 0 0 0 3px #e01b24; }"
    if outline in code:  # (another round: another change)
        code = code.replace(outline, outline.replace("#e01b24", "#3584e4"))
    else:
        code = code.replace(".capture-bar { padding: 4px 6px; }", ".capture-bar { padding: 4px 6px; }\n" + outline)
    with open("widgets.py", "w", encoding="utf-8") as f:
        f.write(code)
    sent_back = "failed when rendered" in prompt
    scene = "no-such-scene" if mode == "bad-scene" or (mode == "bad-scene-first" and not sent_back) else "01-empty"
    with open("fix-shots.json", "w", encoding="utf-8") as f:
        json.dump([{"scene": scene, "label": "The empty chat"}], f)
say(type="result", result="The input box's outline is stronger (`.composer-box`).", session_id="fix-session",
    is_error=False)
