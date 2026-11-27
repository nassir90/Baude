"""The database view's data: a running database's tables, columns and keys, normalized, and their rows, with no GTK, for the view and for a host process asked by a head elsewhere."""
import json
import re
import threading
import time

import lookup  # noqa: E402,F401
from lookup import find_spans  # noqa: E402,F401
from lookupkube import (  # noqa: E402,F401
    PG_LISTS, arango_eval, at_once, db_index, db_names, pg_databases_cached, pg_query, pg_servers_cached,
)
from baudelib import wording  # noqa: E402


PG_SCHEMA_SQL = """select json_build_object(
 'columns', (select coalesce(json_agg(json_build_object('s', c.table_schema, 't', c.table_name, 'c', c.column_name,
     'type', case when c.data_type in ('USER-DEFINED', 'ARRAY') then c.udt_name else c.data_type end,
     'null', c.is_nullable = 'YES', 'def', c.column_default) order by c.table_schema, c.table_name,
     c.ordinal_position), '[]') from information_schema.columns c
   where c.table_schema not in ('pg_catalog', 'information_schema') and c.table_schema not like 'pg_toast%'),
 'pks', (select coalesce(json_agg(json_build_object('s', n.nspname, 't', r.relname, 'c', a.attname)), '[]')
   from pg_constraint k join pg_class r on r.oid = k.conrelid join pg_namespace n on n.oid = r.relnamespace
   join pg_attribute a on a.attrelid = k.conrelid and a.attnum = any(k.conkey)
   where k.contype = 'p' and n.nspname not in ('pg_catalog', 'information_schema')),
 'fks', (select coalesce(json_agg(json_build_object('s', n1.nspname, 't', c1.relname, 'c', a1.attname,
     'rs', n2.nspname, 'rt', c2.relname, 'rc', a2.attname)), '[]')
   from pg_constraint k join pg_class c1 on c1.oid = k.conrelid join pg_namespace n1 on n1.oid = c1.relnamespace
   join pg_class c2 on c2.oid = k.confrelid join pg_namespace n2 on n2.oid = c2.relnamespace
   cross join lateral unnest(k.conkey, k.confkey) as u(a, b)
   join pg_attribute a1 on a1.attrelid = k.conrelid and a1.attnum = u.a
   join pg_attribute a2 on a2.attrelid = k.confrelid and a2.attnum = u.b where k.contype = 'f'),
 'tables', (select coalesce(json_agg(json_build_object('s', n.nspname, 't', c.relname,
     'rows', greatest(c.reltuples, 0)::bigint, 'kind', c.relkind)), '[]')
   from pg_class c join pg_namespace n on n.oid = c.relnamespace where c.relkind in ('r', 'p', 'v', 'm', 'f')
   and n.nspname not in ('pg_catalog', 'information_schema') and n.nspname not like 'pg_toast%'))"""


PG_SECRET = re.compile(r"pass|secret|token|api_?key|private|hash|salt|otp|credential|session_key", re.I)


PG_CACHE = {}  # (container, database) -> (when, schema): a schema is read again after a minute


def pg_schema(server, database, max_age=60):
    """A database's schema: {"tables": {(schema, name): {"schema", "name", "kind", "rows", "columns":
    [{"name", "type", "null", "default", "pk", "fk"}]}}, "links": [((s, t), column, (s, t), column)]}."""
    key = (server["container"], database)
    if key in PG_CACHE and time.time() - PG_CACHE[key][0] < max_age:
        return PG_CACHE[key][1]
    schema = arango_schema(server, database) if server.get("engine") == "arango" else \
        pg_normalize(json.loads(pg_query(server, database, PG_SCHEMA_SQL) or "{}"))
    PG_CACHE[key] = (time.time(), schema)
    return schema


_FORGOTTEN = [0]


_LOCATING, _LOCATING_LOCK = {}, threading.Lock()  # (table, column, hint, _FORGOTTEN) -> [done, answer]: on its way


def db_locate(table=None, column=None, hint=""):
    """db_find's answer; one already on its way for the same name (a hover started it: see BaudeWindow.hover_link)
    is waited for rather than asked again."""
    key = (table, column, hint, _FORGOTTEN[0])
    with _LOCATING_LOCK:
        job, mine = _LOCATING.get(key), key not in _LOCATING
        if mine:
            job = _LOCATING[key] = [threading.Event(), None]
    if not mine and job[0].wait(90) and job[1] is not None:
        return job[1]
    answer = None
    try:
        answer = db_find(table, column, hint)
        return answer
    finally:
        if mine:
            with _LOCATING_LOCK:
                job[1] = answer
                _LOCATING.pop(key, None)
            job[0].set()


def db_find(table=None, column=None, hint=""):
    """Where a table (or a column: of table, if it's known) is in the running databases (worker thread):
    {"hits": [(server, database, schema, table)], "best": the one to open, or None when there's no telling
    which (several databases have it, and hint, the path of the code that defines it, names none of them),
    "error": why there are no hits}. A table is looked for by name first (db_index: every database at once, names
    only); only the databases that have it are read whole."""
    try:
        servers = pg_servers_cached()
    except RuntimeError as e:
        return {"hits": [], "best": None, "error": wording.say("databases.docker_unreachable", why=e)}
    if not servers:
        return {"hits": [], "best": None, "error": wording.say("databases.no_postgres")}
    by_container = {s["container"]: s for s in servers}
    if table:
        def places(index):
            return list(dict.fromkeys((c, db) for c, db, t in index.get(table, ()) if t == table and c in by_container))
        found = places(db_index())
        made, indexed = db_indexed()
        missing = any((s["container"], db) not in indexed for s in servers if s.get("engine") != "arango"
                      for db in pg_databases_cached(s))
        if (missing or not found) and time.time() - made > 10:  # (a database it couldn't read, or since made: again)
            found = places(db_index(max_age=0))
    else:  # (a column, its table unknown: every database's tables, read whole)
        found = [(s["container"], db) for s in servers for db in pg_databases_cached(s)]
    hits = []
    for (c, db), schema in zip(found, at_once(lambda p: pg_schema(by_container[p[0]], p[1]), found)):
        if schema is None:
            continue
        for (ns, name), t in sorted(schema["tables"].items(), key=lambda x: (x[0][0] != "public", x[0])):
            has_column = column and any(col["name"] == column for col in t["columns"])
            if (table and name == table and (not column or has_column)) or (not table and has_column):
                hits.append((by_container[c], db, ns, name))
    if not hits:
        said = (wording.say("databases.no_db_table_column", table=table, column=column) if column else
                wording.say("databases.no_db_table", table=table)) if table else \
            wording.say("databases.no_db_column", column=column)
        return {"hits": [], "best": None, "error": said}
    words = [w for w in re.split(r"[^a-z0-9]+", hint.lower()) if w]
    parts = [w for w in re.split(r"[/_.-]", hint.lower()) if w]
    named = [h for h in hits if h[1].lower() in words or any(h[1].lower() in part for part in parts)]
    places = {(h[0]["container"], h[1], h[3]) for h in hits}  # (a table in two schemas of one database: public)
    picked = {(h[0]["container"], h[1], h[3]) for h in named}
    best = hits[0] if len(places) == 1 else named[0] if len(picked) == 1 else None
    return {"hits": hits, "best": best, "error": None}


def db_indexed():
    """(when the names index was made, {(container, database)} it has), or (0, set()) before there's one."""
    hit = PG_LISTS.get(("index",))
    return (hit[0], hit[2]) if hit is not None else (0, set())


def db_label(database):
    """A database's name as a person reads it."""
    return database


def pg_normalize(raw):
    """PG_SCHEMA_SQL's answer as tables (with their columns, keys marked) and foreign-key links."""
    tables = {(t["s"], t["t"]): {"schema": t["s"], "name": t["t"], "kind": t.get("kind", "r"),
                                 "rows": t.get("rows") or 0, "columns": []} for t in raw.get("tables") or []}
    for c in raw.get("columns") or []:
        if (c["s"], c["t"]) in tables:
            tables[(c["s"], c["t"])]["columns"].append({"name": c["c"], "type": c.get("type") or "", "null": c.get("null"),
                                                         "default": c.get("def"), "pk": False, "fk": None})
    cols = {(t, c["name"]): c for t, tab in tables.items() for c in tab["columns"]}
    for k in raw.get("pks") or []:
        if (col := cols.get(((k["s"], k["t"]), k["c"]))) is not None:
            col["pk"] = True
    links = []
    for k in raw.get("fks") or []:
        src, dst = (k["s"], k["t"]), (k["rs"], k["rt"])
        if src in tables and dst in tables and (col := cols.get((src, k["c"]))) is not None:
            col["fk"] = f"{k['rs']}.{k['rt']}.{k['rc']}" if k["rs"] != k["s"] else f"{k['rt']}.{k['rc']}"
            links.append((src, k["c"], dst, k["rc"]))
    return {"tables": tables, "links": links}


def pg_rows(server, database, schema, table, limit=50):
    """A table's first rows ([{column: value}]), read-only; values of secret-looking columns hidden."""
    if server.get("engine") == "arango":
        return arango_rows(server, database, table, limit)
    qi = lambda s: '"' + s.replace('"', '""') + '"'  # noqa: E731
    sql = f"select coalesce(json_agg(r), '[]') from (select * from {qi(schema)}.{qi(table)} limit {int(limit)}) r"
    return pg_mask(json.loads(pg_query(server, database, sql) or "[]"))


def pg_select(server, database, sql, limit):
    """A query's rows ([{column: value}], limit of them at most), read-only as pg_query runs it; values of
    secret-looking columns hidden. One statement that reads: it's run as a subquery, so another kind fails."""
    if server.get("engine") == "arango":
        raise ValueError(wording.say("databases.query_postgres"))
    sql = (sql or "").strip().rstrip(";").strip()
    if not sql:
        raise ValueError(wording.say("databases.query_empty"))
    wrapped = f"select coalesce(json_agg(r), '[]') from (select * from (\n{sql}\n) q limit {int(limit)}) r"
    return pg_mask(json.loads(pg_query(server, database, wrapped) or "[]"))


def pg_mask(rows):
    """Rows with the values of secret-looking columns (password, token, api_key, hash...) hidden."""
    return [{k: ("•••" if PG_SECRET.search(k) and v is not None else v) for k, v in row.items()} for row in rows]


# ArangoDB, beside Postgres: a server is {"container", "engine": "arango", "version", "exec"?}, found in Docker (an
# arangodb image) or in a local cluster's pod. arangosh runs inside its container (docker / kubectl exec), as root with
# the container's own password: its shell reads $ARANGO_ROOT_PASSWORD there, so Baude never sees it. Collections are
# the diagram's cards (their attributes, sampled: names and types, never values); an edge collection's _from / _to, or
# a collection of id pairs (from_id / to_id…), its links.


ARANGO_SCHEMA_JS = r"""
db._useDatabase(DATABASE);
var cols = db._collections().filter(c => !c.name().startsWith("_"));
var out = {tables: [], links: [], graphs: []}, rels = [];
var PAIRS = [["from_id", "to_id"], ["from", "to"], ["source", "target"], ["source_id", "target_id"], ["src", "dst"]];
cols.forEach(c => {
  var n = c.name(), edge = c.type() === 3, count = c.count();
  var attrs = count ? db._query("FOR d IN @@c LIMIT 50 FOR a IN ATTRIBUTES(d, true) COLLECT name = a, " +
                                "type = TYPENAME(d[a]) WITH COUNT INTO k RETURN {name, type, k}", {"@c": n}).toArray() : [];
  out.tables.push({name: n, edge: edge, count: count, sampled: Math.min(count, 50), attrs: attrs});
  var names = attrs.map(a => a.name);
  var pair = edge ? ["_from", "_to"] : PAIRS.find(p => names.indexOf(p[0]) >= 0 && names.indexOf(p[1]) >= 0);
  if (pair || /^r_/.test(n)) rels.push({name: n, edge: edge, pair: pair || ["from_id", "to_id"], count: count});
});
rels.forEach(r => {
  if (r.edge && r.count) {
    r.ends = db._query("FOR e IN @@c LIMIT 200 COLLECT f = PARSE_IDENTIFIER(e._from).collection, " +
                       "t = PARSE_IDENTIFIER(e._to).collection RETURN [f, t]", {"@c": r.name}).toArray();
  } else if (r.count) {
    r.ids = db._query("FOR d IN @@c LIMIT 5 RETURN [d[@a], d[@b]]", {"@c": r.name, a: r.pair[0], b: r.pair[1]}).toArray();
  }
});
var wanted = [];
rels.forEach(r => (r.ids || []).forEach(p => p.forEach(v => { if (typeof v === "string" && wanted.indexOf(v) < 0) wanted.push(v); })));
var where = {};
if (wanted.length) cols.filter(c => c.type() !== 3 && !/^r_/.test(c.name()) && c.count()).forEach(c => {
  db._query("FOR d IN @@c FILTER d.id IN @ids OR d._key IN @ids RETURN d.id IN @ids ? d.id : d._key",
            {"@c": c.name(), ids: wanted}).toArray().forEach(v => { where[v] = c.name(); });
});
rels.forEach(r => {
  var pairs = r.ends || (r.ids || []).map(p => [where[p[0]], where[p[1]]]), seen = {};
  pairs.forEach(p => { if (p[0] && p[1] && !seen[p]) { seen[p] = 1;
    out.links.push({rel: r.name, from_attr: r.pair[0], to_attr: r.pair[1], from: p[0], to: p[1]}); } });
  if (!Object.keys(seen).length) out.links.push({rel: r.name, from_attr: r.pair[0], to_attr: r.pair[1], from: null, to: null});
});
try { out.graphs = require("@arangodb/general-graph")._listObjects().map(g => ({name: g._key, edges: g.edgeDefinitions})); } catch (e) {}
print(JSON.stringify(out));
"""


def arango_schema(server, database):
    """A database's collections as the schema view draws tables ({"tables", "links"}: see pg_schema)."""
    raw = arango_eval(server, f"var DATABASE = {json.dumps(database)};" + ARANGO_SCHEMA_JS)
    return arango_normalize(raw)


def _arango_snake(name):
    """CalendarEvent → CALENDAR_EVENT (an entity's name as a relation's name spells it)."""
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name).upper()


def arango_guess_ends(rel, names):
    """A relation's ends from its name, when it has no documents to tell (r_JOB_FOR_CUSTOMER joins
    e_Job to e_Customer): the collections whose names start and end it, the longest of each."""
    spelt = {_arango_snake(re.sub(r"^e_", "", n)): n for n in names if not n.startswith("r_")}
    words = re.sub(r"^r_", "", rel).upper()
    start = max((s for s in spelt if words.startswith(s + "_")), key=len, default=None)
    end = max((s for s in spelt if words.endswith("_" + s) and words != s), key=len, default=None)
    return spelt.get(start), spelt.get(end)


def arango_normalize(raw):
    """arangosh's answer (ARANGO_SCHEMA_JS) as tables and links: each collection a table in the namespace
    "collections" (_key first, then its sampled attributes, a type for each, nullable if a sampled document lacked
    it); a relation's two attributes link to the collections they join."""
    ns, tables = "collections", {}
    for t in raw.get("tables") or []:
        cols = [{"name": "_key", "type": "key", "null": False, "default": None, "pk": True, "fk": None}]
        seen = {}
        for a in t.get("attrs") or []:
            seen.setdefault(a["name"], []).append(a)
        for name in sorted(seen, key=lambda n: (n.startswith("_"), n.lower())):
            if name in ("_key", "_id", "_rev"):
                continue
            types = sorted({a["type"] for a in seen[name]} - {"null"}) or ["null"]
            present = sum(a["k"] for a in seen[name] if a["type"] != "null")
            cols.append({"name": name, "type": "|".join(types), "null": present < (t.get("sampled") or 0),
                         "default": None, "pk": False, "fk": None})
        tables[(ns, t["name"])] = {"schema": ns, "name": t["name"], "kind": "e" if t.get("edge") else "r",
                                   "rows": t.get("count") or 0, "columns": cols}
    links, names = [], [n for _s, n in tables]
    for l in raw.get("links") or []:
        rel = (ns, l["rel"])
        if rel not in tables:
            continue
        ends = (l.get("from"), l.get("to")) if l.get("from") and l.get("to") else arango_guess_ends(l["rel"], names)
        for attr, target in zip((l["from_attr"], l["to_attr"]), ends):
            if not target or (ns, target) not in tables:
                continue
            cols = tables[rel]["columns"]
            col = next((c for c in cols if c["name"] == attr), None)
            if col is None:  # (an empty collection: the attribute it would have)
                col = {"name": attr, "type": "id", "null": False, "default": None, "pk": False, "fk": None}
                cols.append(col)
            key = "id" if any(c["name"] == "id" for c in tables[(ns, target)]["columns"]) and not attr.startswith("_") \
                else "_key"
            col["fk"] = f"{target}.{key}"
            links.append((rel, attr, (ns, target), key))
    return {"tables": tables, "links": links}


def arango_rows(server, database, collection, limit=50):
    """A collection's first documents (without _id and _rev), their secret-looking attributes hidden."""
    js = (f"db._useDatabase({json.dumps(database)}); print(JSON.stringify(db._query(\"FOR d IN @@c LIMIT {int(limit)} "
          f"RETURN UNSET(d, '_id', '_rev')\", {{\"@c\": {json.dumps(collection)}}}).toArray()));")
    rows = arango_eval(server, js)
    return pg_mask([{k: (json.dumps(v) if isinstance(v, (dict, list)) else v) for k, v in r.items()} for r in rows])


def schema_layout(sizes, links, gap=56):
    """Positions for table cards (sizes: [(w, h)]; links: [(child, parent)], a child holding a foreign key to
    its parent): linked tables in columns, each table one column right of the tables it references, ordered
    to keep links short; tables without links in a grid beside them. Returns [(x, y)]."""
    n = len(sizes)
    parents, children = [set() for _ in range(n)], [set() for _ in range(n)]
    for c, p in links:
        if c != p:
            parents[c].add(p)
            children[p].add(c)
    rank = {}
    for start in range(n):  # longest chain of references below each table (iterative: no recursion limit)
        stack = [(start, iter(parents[start]))]
        onpath = {start}
        while stack:
            node, it = stack[-1]
            nxt = next((p for p in it if p not in rank and p not in onpath), None)
            if nxt is not None:
                stack.append((nxt, iter(parents[nxt])))
                onpath.add(nxt)
                continue
            rank[node] = 1 + max((rank[p] for p in parents[node] if p in rank), default=-1)
            onpath.discard(node)
            stack.pop()
    linked = [i for i in range(n) if parents[i] or children[i]]
    columns = {}
    for i in linked:
        columns.setdefault(rank[i], []).append(i)
    pos, order, x = [None] * n, {}, 0.0
    for r in sorted(columns):
        col = columns[r]
        col.sort(key=lambda i: (sum(order.get(p, 0) for p in parents[i]) / max(1, len(parents[i]))
                                if parents[i] else -1, -len(children[i]), i))
        y = 0.0
        for k, i in enumerate(col):
            pos[i], order[i] = (x, y), k
            y += sizes[i][1] + gap / 2
        x += max(sizes[i][0] for i in col) + gap * 1.6
    rest = [i for i in range(n) if pos[i] is None]
    if rest:  # a grid of columns about as tall as the linked part (or a square, alone)
        area = sum(sizes[i][0] * sizes[i][1] for i in rest)
        height = max([sizes[i][1] for i in rest] +
                     [max((p[1] + sizes[i][1] for i, p in enumerate(pos) if p), default=0), (area * 0.6) ** 0.5])
        col_w = y = 0.0
        for i in sorted(rest, key=lambda i: -sizes[i][1]):
            if y and y + sizes[i][1] > height:
                x, y, col_w = x + col_w + gap, 0.0, 0.0
            pos[i] = (x, y)
            y += sizes[i][1] + gap / 2
            col_w = max(col_w, sizes[i][0])
    return pos
