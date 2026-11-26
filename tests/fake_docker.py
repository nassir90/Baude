#!/usr/bin/env python3
"""A stand-in for docker for the Docker view's tests and screenshots (BAUDE_DOCKER points at it): two contexts,
default and colima, a made-up machine each, answered the way docker answers with --format json. colima's runs a
Compose project, shop (its api, db and a worker restarting), and a redis on its own; default's daemon isn't running.
Every call is logged ($FAKE_DOCKER_LOG). With $FAKE_DOCKER_STATE (a file), start, stop, restart and remove are
remembered. `logs` prints a few lines, and with -f waits to be stopped, printing a line every $FAKE_DOCKER_FOLLOW
seconds if that's set (never for more than two minutes). `exec -it` is a shell (this machine's sh) in the container."""
import json
import os
import sys
import tempfile
import time

NOW = time.time()


def at(minutes_ago):
    return time.strftime("%Y-%m-%d %H:%M:%S +0000 UTC", time.gmtime(NOW - minutes_ago * 60))


def ctr(cid, name, image, state, status, minutes, ports="", project=""):
    labels = f"com.docker.compose.project={project},com.docker.compose.service={name.split('-')[1]}" if project else ""
    return {"ID": cid * 4, "Names": name, "Image": image, "State": state, "Status": status, "CreatedAt": at(minutes),
            "Ports": ports, "Labels": labels, "RunningFor": "", "Command": "", "Mounts": "", "Networks": "bridge"}


CONTAINERS = [
    ctr("3f9c2a1b7d4e8f60", "shop-api-1", "shop/api:1.4", "running", "Up 2 hours", 125,
        "0.0.0.0:8080->80/tcp", "shop"),
    ctr("8a1d4c7e2b9f3a51", "shop-db-1", "postgres:16", "running", "Up 2 hours (healthy)", 126, "5432/tcp", "shop"),
    ctr("c2e7f19a4d6b0835", "shop-worker-1", "shop/worker:1.4", "restarting", "Restarting (1) 4 seconds ago", 125,
        "", "shop"),
    ctr("5b8e0d3a6c1f7942", "cache", "redis:7", "running", "Up 3 days", 4400, "6379/tcp"),
    ctr("e4a7c0b9d2f51836", "migrate-once", "shop/api:1.4", "exited", "Exited (0) 2 hours ago", 130),
    ctr("9d3b6f0e1a8c4527", "smoke-test", "alpine:3.20", "exited", "Exited (1) 20 minutes ago", 25),
]
IMAGES = [
    {"Repository": "shop/api", "Tag": "1.4", "ID": "sha256:" + "a1b2c3d4e5f6" * 5 + "abcd", "Size": "187MB",
     "CreatedAt": at(300)},
    {"Repository": "shop/worker", "Tag": "1.4", "ID": "sha256:" + "b2c3d4e5f6a1" * 5 + "bcde", "Size": "164MB",
     "CreatedAt": at(300)},
    {"Repository": "postgres", "Tag": "16", "ID": "sha256:" + "c3d4e5f6a1b2" * 5 + "cdef", "Size": "432MB",
     "CreatedAt": at(20000)},
    {"Repository": "redis", "Tag": "7", "ID": "sha256:" + "d4e5f6a1b2c3" * 5 + "def0", "Size": "117MB",
     "CreatedAt": at(30000)},
    {"Repository": "alpine", "Tag": "3.20", "ID": "sha256:" + "e5f6a1b2c3d4" * 5 + "ef01", "Size": "7.8MB",
     "CreatedAt": at(50000)},
    {"Repository": "<none>", "Tag": "<none>", "ID": "sha256:" + "f6a1b2c3d4e5" * 5 + "f012", "Size": "187MB",
     "CreatedAt": at(2000)},
]
VOLUMES = [
    {"Name": "shop_pgdata", "Driver": "local", "Labels": "com.docker.compose.project=shop",
     "Mountpoint": "/var/lib/docker/volumes/shop_pgdata/_data"},
    {"Name": "redis-data", "Driver": "local", "Labels": "", "Mountpoint": "/var/lib/docker/volumes/redis-data/_data"},
]
NETWORKS = [
    {"Name": "bridge", "Driver": "bridge", "Scope": "local", "ID": "7e1f" * 16, "CreatedAt": at(50000)},
    {"Name": "host", "Driver": "host", "Scope": "local", "ID": "2a9c" * 16, "CreatedAt": at(50000)},
    {"Name": "none", "Driver": "null", "Scope": "local", "ID": "5d3e" * 16, "CreatedAt": at(50000)},
    {"Name": "shop_default", "Driver": "bridge", "Scope": "local", "ID": "9b4a" * 16, "CreatedAt": at(126)},
]
PROJECTS = [{"Name": "shop", "Status": "running(2), restarting(1)", "ConfigFiles": "/srv/shop/compose.yaml"}]
LOG = ["2026-11-26T13:00:01Z listening on :80", "2026-11-26T13:00:04Z GET /healthz 200 0.9ms",
       "2026-11-26T13:01:12Z GET /orders 200 14.2ms", "2026-11-26T13:01:13Z POST /orders 201 31.0ms",
       "2026-11-26T13:02:40Z GET /orders/1182 200 6.1ms"]


def state():
    path = os.environ.get("FAKE_DOCKER_STATE")
    if path and os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {}


def keep(s):
    path = os.environ.get("FAKE_DOCKER_STATE")
    if path:
        with open(path, "w") as f:
            json.dump(s, f)


def lines(objs):
    return "".join(json.dumps(o) + "\n" for o in objs)


def main(argv):
    if os.environ.get("FAKE_DOCKER_LOG"):
        with open(os.environ["FAKE_DOCKER_LOG"], "a") as f:
            f.write(json.dumps(argv) + "\n")
    context = "colima"
    while argv and argv[0] in ("--context", "--host", "-H", "-c"):
        context, argv = argv[1], argv[2:]
    if argv[:2] == ["context", "ls"]:
        sys.stdout.write(lines([{"Name": "colima", "Current": True, "DockerEndpoint": "unix:///fixture/colima.sock"},
                                {"Name": "default", "Current": False, "DockerEndpoint": "unix:///var/run/docker.sock"}]))
        return 0
    if context == "default":
        sys.stderr.write("Cannot connect to the Docker daemon at unix:///var/run/docker.sock. "
                         "Is the docker daemon running?\n")
        return 1
    s = state()
    gone, states = set(s.get("gone", [])), s.get("states", {})
    ctrs = [dict(c, **states.get(c["ID"], {})) for c in CONTAINERS if c["ID"] not in gone]
    cmd = argv[0] if argv else ""
    if cmd == "ps":
        shown = ctrs if "--all" in argv or "-a" in argv else [c for c in ctrs if c["State"] in ("running", "restarting")]
        sys.stdout.write(lines(shown))
    elif cmd == "image" and argv[1] == "ls":
        sys.stdout.write(lines([i for i in IMAGES if i["ID"] not in gone]))
    elif cmd == "volume" and argv[1] == "ls":
        sys.stdout.write(lines([v for v in VOLUMES if v["Name"] not in gone]))
    elif cmd == "network" and argv[1] == "ls":
        sys.stdout.write(lines([n for n in NETWORKS if n["ID"] not in gone]))
    elif cmd == "compose" and argv[1] == "ls":
        sys.stdout.write(json.dumps([p for p in PROJECTS if p["Name"] not in gone]) + "\n")
    elif cmd == "compose" and argv[1] == "-p":
        name, verb = argv[2], argv[3]
        if verb == "ps":
            sys.stdout.write(lines([{"Name": c["Names"], "Service": c["Names"].split("-")[1], "Image": c["Image"],
                                     "State": c["State"], "Status": c["Status"]} for c in ctrs
                                    if f"project={name}" in c["Labels"]]))
        elif verb == "logs":
            return logs(argv)
        else:
            if verb == "down":
                s.setdefault("gone", []).append(name)
                keep(s)
            sys.stderr.write(f" Container {name} {verb.capitalize()}ed\n")
    elif cmd in ("start", "stop", "restart"):
        cid = argv[-1]
        st = {"start": ("running", "Up Less than a second"), "restart": ("running", "Up Less than a second"),
              "stop": ("exited", "Exited (0) Less than a second ago")}[cmd]
        s.setdefault("states", {})[cid] = {"State": st[0], "Status": st[1]}
        keep(s)
        print(cid)
    elif cmd == "rm" or cmd in ("image", "volume", "network") and argv[1] == "rm":
        oid = argv[-1]
        s.setdefault("gone", []).append(oid)
        keep(s)
        print(oid)
    elif cmd in ("container", "image", "volume", "network") and argv[1] == "inspect":
        oid = argv[2]
        if cmd == "container":
            c = next((c for c in ctrs if c["ID"] == oid), ctrs[0])
            got = {"Id": c["ID"], "Created": c["CreatedAt"], "Name": "/" + c["Names"],
                   "State": {"Status": c["State"], "Running": c["State"] == "running", "ExitCode": 0},
                   "Config": {"Image": c["Image"], "Env": ["PORT=80", "LOG_LEVEL=info"], "Cmd": ["serve"],
                              "Labels": dict(p.split("=", 1) for p in c["Labels"].split(",") if p)},
                   "HostConfig": {"RestartPolicy": {"Name": "unless-stopped"}},
                   "NetworkSettings": {"Networks": {"shop_default": {"IPAddress": "172.18.0.3"}}}}
        else:
            got = {"Name": oid, "Driver": "local"}
        print(json.dumps([got], indent=4))
    elif cmd == "logs":
        return logs(argv)
    elif cmd == "exec":
        os.environ["PS1"] = "/app # "
        app = os.path.join(os.environ.get("BAUDE_RUNTIME_DIR") or tempfile.mkdtemp(prefix="fake-docker-"), "fake-docker-app")  # (the container's /app, made up)
        os.makedirs(app, exist_ok=True)
        for name in ("app.py", "requirements.txt", "compose.yaml"):
            open(os.path.join(app, name), "a").close()
        os.chdir(app)
        os.execvp("sh", ["sh", "-i"])
    else:
        sys.stderr.write(f"docker: '{cmd}' is not a docker command.\n")
        return 1
    return 0


def logs(argv):
    for line in LOG:
        print(line, flush=True)
    if "-f" in argv:
        every = float(os.environ.get("FAKE_DOCKER_FOLLOW") or 0)
        end = time.monotonic() + 120
        while time.monotonic() < end:
            time.sleep(every or 1)
            if every:
                print(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()) + " GET /healthz 200 0.8ms", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
