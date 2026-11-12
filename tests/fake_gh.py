#!/usr/bin/env python3
"""A stand-in for the gh CLI, for the Feedback tab and its fixer: GitHub's issues and pull requests, in a JSON file
(FAKE_GH_STATE: {viewer, issues, prs, calls, issues_on: {owner/name: the issues of a private repository it shows you}}).
What's changed is what `gh` would change; every call's kept in calls.
And `gh api`, as lookup.GITHUB asks it (rest): {rest: {owner/name: [issues as GitHub's REST API says them]}}."""
import fcntl
import hashlib
import json
import os
import re
import sys
import time
import urllib.parse


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def opt(args, name, default=None):
    return args[args.index(name) + 1] if name in args else default


def issue(state, number):
    return next(i for i in state["issues"] if i["number"] == int(number))


def pr(state, number):
    return next(p for p in state["prs"] if p["number"] == int(number))


def comment(state, number, body):
    rival = os.environ.get("FAKE_GH_RIVAL")  # (another machine, a moment ahead of this one on the same round)
    if rival and '"state": "working"' in body:
        ahead = re.sub(r'"machine": "[^"]*"', f'"machine": "{rival}"', body)
        issue(state, number).setdefault("comments", []).append(
            {"author": {"login": "other"}, "authorAssociation": "OWNER", "body": ahead, "createdAt": now()})
    issue(state, number).setdefault("comments", []).append(
        {"author": {"login": state.get("login", "you")}, "authorAssociation": "OWNER", "body": body, "createdAt": now()})
    issue(state, number)["updatedAt"] = now()


def rest(state, args):
    """gh api -i, a GET of a repository's issues or pull requests, as the real one does it: signed out, it asks no one
    and exits 4; offline, no answer; else what GitHub answered, first line and headers (a new line, then \\r\\n) and the
    body, exiting 1 on anything but a 2xx, a 304 too. FAKE_GH_OFFLINE: no answer. In the state: rest_status {owner/name:
    a status to answer with}, rest_headers {more headers}, no_etag, issues_off [owner/name: /issues says 410], left (the
    rate limit: what's left of it, a 200 taking one; a 304 doesn't)."""
    if os.environ.get("FAKE_GH_SIGNED_IN", "1") != "1":
        print("To get started with GitHub CLI, please run:  gh auth login", file=sys.stderr)
        return 4
    if os.environ.get("FAKE_GH_OFFLINE"):
        print("error connecting to api.github.com", file=sys.stderr)
        return 1
    if opt(args, "--method", "GET") != "GET" or {"-f", "-F", "--field", "--raw-field", "--input"} & set(args):
        print("fake gh: only ever read", file=sys.stderr)
        state["wrote"] = True
        return 1
    url = urllib.parse.urlsplit(args[-1])
    m = re.fullmatch(r"repos/([^/]+/[^/]+)/(issues|pulls)", url.path)
    query = dict(urllib.parse.parse_qsl(url.query))
    repo, kind = m.groups() if m else (None, None)
    headers = {"X-Ratelimit-Limit": "5000", "X-Ratelimit-Remaining": str(state.get("left", 4900)),
               "X-Ratelimit-Reset": str(int(time.time()) + 1800), **state.get("rest_headers", {})}
    status = state.get("rest_status", {}).get(repo) or \
        (410 if kind == "issues" and repo in state.get("issues_off", ()) else None) or \
        (200 if repo in state.get("rest", {}) else 404)
    body, match = "", [v for a, v in zip(args, args[1:]) if a == "-H" and v.startswith("If-None-Match: ")]
    if status == 200:
        items = [i for i in state["rest"][repo] if query.get("state") == "all" or i["state"] == query.get("state", "open")]
        if kind == "pulls":
            items = [{**{k: v for k, v in i.items() if k != "pull_request"}, "merged_at": i["pull_request"]["merged_at"]}
                     for i in items if "pull_request" in i]
        items.sort(key=lambda i: i["updated_at"], reverse=True)
        body = json.dumps(items[:int(query.get("per_page", 30))])
        etag = hashlib.sha1(body.encode()).hexdigest()
        if not state.get("no_etag"):
            headers["Etag"] = f'W/"{etag}"'
            if match and match[0].endswith(f'"{etag}"'):
                status, body = 304, ""  # (not counted)
                headers["Etag"] = f'"{etag}"'
        if status == 200:
            state["left"] = state.get("left", 4900) - 1
    words = {200: "OK", 304: "Not Modified", 401: "Unauthorized", 403: "Forbidden", 404: "Not Found", 410: "Gone",
             429: "Too Many Requests", 500: "Internal Server Error"}
    sys.stdout.write(f"HTTP/2.0 {status} {words[status]}\n" + "".join(f"{k}: {v}\r\n" for k, v in headers.items()) + "\r\n")
    sys.stdout.write(body)
    if status >= 300:
        print(f"gh: HTTP {status}", file=sys.stderr)
        return 1
    return 0


def run(state, args):
    what = args[:2]
    if what == ["auth", "status"]:
        return 0 if os.environ.get("FAKE_GH_SIGNED_IN", "1") == "1" else 1
    if args[:1] == ["api"] and "-i" in args:
        return rest(state, args)
    if what == ["repo", "view"]:
        name = args[2] if len(args) > 2 else ""
        if name.endswith("Internal") and name not in state.get("issues_on", {}):  # (fixes.PRIVATE_FEEDBACK's)
            print(f"GraphQL: Could not resolve to a Repository with the name '{name}'. (repository)", file=sys.stderr)
            return 1
        print(state.get("viewer", "ADMIN"))
    elif what == ["issue", "list"]:
        print(json.dumps(state.get("issues_on", {}).get(opt(args, "--repo"), state["issues"])))
    elif what == ["pr", "list"]:
        if os.environ.get("FAKE_GH_SIGNED_IN", "1") != "1":  # (signed out, as the real one: it asks no one, exits 4)
            print("To get started with GitHub CLI, please run:  gh auth login", file=sys.stderr)
            return 4
        print(json.dumps(state["prs"]))
    elif what == ["issue", "comment"]:
        comment(state, args[2], sys.stdin.read())
    elif what == ["issue", "close"]:
        i = issue(state, args[2])
        i["state"], i["stateReason"] = "CLOSED", "NOT_PLANNED" if opt(args, "--reason") == "not planned" else "COMPLETED"
        if opt(args, "--comment"):
            comment(state, args[2], opt(args, "--comment"))
    elif what == ["issue", "reopen"]:
        i = issue(state, args[2])
        i["state"], i["stateReason"] = "OPEN", "REOPENED"
    elif what == ["pr", "create"]:
        body = sys.stdin.read()
        n = 100 + len(state["prs"])
        closes = [int(m) for m in re.findall(r"Fixes #(\d+)", body)]
        state["prs"].append({"number": n, "title": opt(args, "--title"), "state": "OPEN", "isDraft": False,
                             "headRefName": opt(args, "--head"), "headRefOid": "sha-" + opt(args, "--head"), "url": f"https://github.com/o/r/pull/{n}", "body": body,
                             "mergedAt": None, "updatedAt": now(),
                             "closingIssuesReferences": [{"number": c} for c in closes]})
        print(f"https://github.com/o/r/pull/{n}")
    elif what == ["pr", "merge"]:
        p = pr(state, args[2])
        p["state"], p["mergedAt"] = "MERGED", now()
        for c in p["closingIssuesReferences"]:
            i = issue(state, c["number"])
            i["state"], i["stateReason"] = "CLOSED", "COMPLETED"
    elif what == ["pr", "close"]:
        pr(state, args[2])["state"] = "CLOSED"
    else:
        print(f"fake gh: nothing for {args}", file=sys.stderr)
        return 1
    return 0


def main():
    args = sys.argv[1:]
    with open(os.environ["FAKE_GH_STATE"] + ".lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with open(os.environ["FAKE_GH_STATE"], encoding="utf-8") as f:
            state = json.load(f)
        state.setdefault("calls", []).append(args)
        if os.environ.get("FAKE_GH_FAIL") and " ".join(args[:2]) in os.environ["FAKE_GH_FAIL"].split(","):
            print("fake gh: refused, as told", file=sys.stderr)
            code = 1
        else:
            code = run(state, args)
        if args[0] == "api" and "rest" not in state:
            return code  # (lookup.GITHUB, while a test of something else runs: its state left to it)
        with open(os.environ["FAKE_GH_STATE"] + ".new", "w", encoding="utf-8") as f:
            json.dump(state, f)
        os.replace(os.environ["FAKE_GH_STATE"] + ".new", os.environ["FAKE_GH_STATE"])  # (a test may be reading it)
    return code


if __name__ == "__main__":
    sys.exit(main())
