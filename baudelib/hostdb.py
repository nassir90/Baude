"""hostdb — the running databases here, read for a head elsewhere (the phone's Database Schemas): the servers Docker (and
a local cluster) has and their databases (servers), a database's tables and foreign keys (schema), a table's first rows
(rows). The desktop's own functions (schemadata), read-only, a secret-looking column's values masked there. No GTK."""
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:  # (as server.catalogue has it: the modules at the root)
    sys.path.insert(0, ROOT)

import lookupkube  # noqa: E402
import schemadata  # noqa: E402
from baudelib import deps, wording  # noqa: E402


def _server(container):
    server = next((s for s in lookupkube.pg_servers_cached() if s["container"] == container), None)
    if server is None:
        raise ValueError(wording.say("databases.no_postgres"))
    return server


def _servers():
    try:
        servers = lookupkube.pg_servers_cached()
        if not servers and (missing := deps.missing("docker")) is not None:  # (none found, and no Docker to look in)
            return {"missing": missing}
        if not servers:
            return {"error": wording.say("databases.no_postgres")}
        return {"servers": [{"container": s["container"], "engine": s.get("engine") or "postgres",
                             "version": s.get("version") or ""} for s in servers],
                "databases": {s["container"]: lookupkube.pg_databases_cached(s) for s in servers}}
    except RuntimeError as e:
        if (missing := deps.missing("docker")) is not None:
            return {"missing": missing}
        return {"error": wording.say("databases.docker_unreachable", why=str(e))}


def _text(v):
    return None if v is None else json.dumps(v) if isinstance(v, (dict, list)) else str(v)


def answer(server, msg):
    """The reply's fields for a databases request."""
    what = msg.get("what")
    try:
        if what == "servers":
            return {"what": what, **_servers()}
        container, database = msg.get("container"), msg.get("database")
        if what == "schema":
            t0 = time.monotonic()
            schema = schemadata.pg_schema(_server(container), database)
            return {"what": what, "took": time.monotonic() - t0,
                    "tables": [{"schema": t["schema"], "name": t["name"], "kind": t["kind"], "rows": int(t["rows"] or 0),
                                "columns": [{"name": c["name"], "type": c["type"], "null": c["null"], "pk": c["pk"],
                                             "fk": c["fk"]} for c in t["columns"]]}
                               for _key, t in sorted(schema["tables"].items())],
                    "links": [{"from_schema": a[0], "from_table": a[1], "from_column": ac, "to_schema": b[0],
                               "to_table": b[1], "to_column": bc} for a, ac, b, bc in schema["links"]]}
        if what == "rows":
            rows = schemadata.pg_rows(_server(container), database, msg.get("schema"), msg.get("table"))
            return {"what": what, "rows": [{k: _text(v) for k, v in r.items()} for r in rows]}
    except (RuntimeError, ValueError) as e:
        return {"what": what, "error": str(e)}
    return {"what": what, "error": "databases takes servers, schema or rows"}
