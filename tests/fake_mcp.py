#!/usr/bin/env python3
"""A stdio MCP server for the tests: answers initialize, then (only once it's been told it's initialized)
tools/list, with two tools. Referencer starts it to read their schemas."""
import json
import sys

TOOLS = [
    {"name": "echo", "description": "Says the text back.",
     "inputSchema": {"type": "object", "required": ["text"], "properties": {
         "text": {"type": "string", "description": "What to say"},
         "times": {"type": "integer", "description": "How many times"},
         "tone": {"enum": ["plain", "loud"]}}}},
    {"name": "status", "description": "How the server is.", "inputSchema": {"type": "object", "properties": {}}},
]

initialized = False
for line in sys.stdin:
    try:
        msg = json.loads(line)
    except ValueError:
        continue
    method, mid = msg.get("method"), msg.get("id")
    if method == "initialize":
        result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                  "serverInfo": {"name": "fake", "version": "1"}}
    elif method == "notifications/initialized":
        initialized = True
        continue
    elif method == "tools/list" and initialized:
        result = {"tools": TOOLS}
    else:
        print(json.dumps({"jsonrpc": "2.0", "id": mid, "error": {"code": -32002, "message": "not initialized"}}),
              flush=True)
        continue
    print(json.dumps({"jsonrpc": "2.0", "id": mid, "result": result}), flush=True)
