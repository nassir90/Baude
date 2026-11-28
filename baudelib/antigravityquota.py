"""Read quota from a running Antigravity language server's local Connect RPC."""
import http.client
import json
import os
import re
import ssl
import subprocess


SERVICE = "/exa.language_server_pb.LanguageServerService/RetrieveUserQuotaSummary"
BUCKETS = {"gemini-5h": "gemini_5_hour", "gemini-weekly": "gemini_7_day",
           "3p-5h": "claude_gpt_5_hour", "3p-weekly": "claude_gpt_7_day"}


def _servers(profile=""):
    """Yield local RPC ports and CSRF tokens only from Antigravity's language-server processes."""
    try:
        processes = subprocess.check_output(["ps", "-axww", "-o", "pid=,ppid=,command="], text=True, timeout=3)
    except (OSError, subprocess.SubprocessError):
        return
    rows = {}
    for line in processes.splitlines():
        match = re.match(r"\s*(\d+)\s+(\d+)\s+(.*)", line)
        if match:
            pid, parent, command = match.groups()
            rows[pid] = parent, command
    home = profile or os.environ.get("BAUDE_ANTIGRAVITY_HOME") or ""
    target = os.path.abspath(os.path.expanduser(home)) if home else None
    for pid, (parent, command) in rows.items():
        hub = bool(re.search(r"(?:^|/)agy(?:\s|$)", command) and
                   ("--hub" in command or re.search(r"\sacp(?:\s|$)", command)))
        if hub and "--hub" not in command and any(ppid == pid and "language_server" in child.lower()
                                                  for ppid, child in rows.values()):
            continue
        if not hub and ("language_server" not in command.lower() or
                        not re.search(r"antigravity|\bagy\b", command, re.I)):
            continue
        ancestors, seen = [], {pid}
        while parent in rows and parent not in seen:
            seen.add(parent)
            parent, ancestor_command = rows[parent]
            ancestors.append(ancestor_command)
        launcher = command if hub else next((item for item in ancestors
                                              if re.search(r"(?:^|/)agy(?:\s|$)", item)), None)
        if launcher is None:
            continue  # an IDE language server may be signed in to a different Google account
        chosen = re.search(r'--gemini-dir(?:=|\s+)("[^"]+"|\S+)', launcher or "")
        chosen = os.path.abspath(os.path.expanduser(chosen.group(1).strip('"'))) if chosen else None
        if target and chosen != target:
            continue
        if not target and chosen:
            continue
        csrf = re.search(r"(?:^|\s)--csrf_token(?:=|\s+)([^\s]+)", command)
        if not csrf and not hub:
            continue
        ports = set()
        explicit = re.search(r"(?:--extension_server_port|--hub-port)(?:=|\s+)(\d+)", command)
        if explicit:
            ports.add(int(explicit.group(1)))
        try:
            listening = subprocess.check_output(["lsof", "-nP", "-a", "-p", pid, "-iTCP", "-sTCP:LISTEN"],
                                                text=True, timeout=3)
        except (OSError, subprocess.SubprocessError):
            listening = ""
        ports.update(int(port) for port in re.findall(r"(?:127\.0\.0\.1|\*|localhost):(\d+)\s*\(LISTEN\)",
                                                      listening))
        for port in sorted(ports):
            yield port, csrf.group(1) if csrf else None


def _summary(port, csrf, secure):
    conn = (http.client.HTTPSConnection("127.0.0.1", port, timeout=2,
                                       context=ssl._create_unverified_context()) if secure else
            http.client.HTTPConnection("127.0.0.1", port, timeout=2))
    try:
        headers = {"Content-Type": "application/json", "Connect-Protocol-Version": "1"}
        if csrf:
            headers["x-codeium-csrf-token"] = csrf
        conn.request("POST", SERVICE, '{"forceRefresh":true}', headers)
        response = conn.getresponse()
        if response.status != 200:
            return None
        body = json.load(response)
        for key in ("response", "summary"):
            if isinstance(body, dict) and isinstance(body.get(key), dict):
                body = body[key]
        return body if isinstance(body, dict) and isinstance(body.get("groups"), list) else None
    finally:
        conn.close()


def read(profile=""):
    """Return [(bucket key, percent used, reset time)] or None until a local server exposes the summary."""
    from baudelib.accounts import _reset_time

    for port, csrf in _servers(profile):
        for secure in (True, False):
            try:
                body = _summary(port, csrf, secure)
            except (OSError, ValueError, http.client.HTTPException, ssl.SSLError):
                continue
            if body is None:
                continue
            found = {}
            for group in body["groups"]:
                if not isinstance(group, dict):
                    continue
                for bucket in group.get("buckets") or []:
                    if not isinstance(bucket, dict):
                        continue
                    key, fraction = BUCKETS.get(bucket.get("bucketId")), bucket.get("remainingFraction")
                    remaining = bucket.get("remaining")
                    if fraction is None and isinstance(remaining, dict):
                        fraction = remaining.get("remainingFraction")
                        if fraction is None and remaining.get("case") == "remainingFraction":
                            fraction = remaining.get("value")
                    if fraction is None and "remainingFraction" not in bucket and bucket.get("resetTime"):
                        fraction = 0  # proto3 JSON omits a numeric zero, including an exhausted bucket
                    if key and isinstance(fraction, (int, float)) and not isinstance(fraction, bool) and \
                            0 <= fraction <= 1 and not bucket.get("disabled"):
                        found[key] = (key, round((1 - fraction) * 100, 1), _reset_time(bucket.get("resetTime")))
            if found:
                return [found[key] for key in BUCKETS.values() if key in found]
    return None
