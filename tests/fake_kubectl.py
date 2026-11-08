#!/usr/bin/env python3
"""A stand-in for kubectl (and helm) for the Kubernetes view's tests: a made-up cluster per context, answered the way
kubectl answers. k3d-dev has a metrics API and one node; prod has neither a metrics API nor just one node, but has
KEDA (its custom resources, which k3d-dev doesn't serve). Every call
is logged ($FAKE_KUBECTL_LOG), so the tests can check which commands ran (only reading ones, but for the deletes they
confirm), and that a Secret's values were never asked for. A test can hold a context's answers back, or take its
cluster down ($FAKE_KUBECTL_HOLD).

`logs` prints a few lines of a pod's container (its previous run's with --previous); with -f it then waits to be
stopped, printing a line every $FAKE_KUBECTL_FOLLOW seconds if that's set (and never for more than two minutes).
`delete pod` says what kubectl says; with $FAKE_KUBECTL_STATE (a file) it's remembered: the pod is Terminating
afterwards, or gone if it was forced."""
import json
import os
import sys
import time

# the cluster's clock is this one: ages, and a restart's "lately", read the same whenever the tests run
NOW = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def ts(minutes_ago):
    import calendar
    base = calendar.timegm(time.strptime(NOW[:19], "%Y-%m-%dT%H:%M:%S"))
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(base - minutes_ago * 60))


def ctr(name="main", ready=True, restarts=0, waiting=None, exited=None, restarted=None):
    """A container's status: running, unless waiting (for a reason) or exited ((reason, exit code)); restarted: its
    last run ended that many minutes ago."""
    state = {"waiting": {"reason": waiting}} if waiting else \
        {"terminated": {"reason": exited[0], "exitCode": exited[1]}} if exited else {"running": {}}
    out = {"name": name, "ready": ready, "restartCount": restarts, "state": state}
    if restarted is not None:
        out["lastState"] = {"terminated": {"reason": "Error", "exitCode": 1, "finishedAt": ts(restarted)}}
    return out


def pod(name, ns, minutes, phase="Running", containers=None, init=(), deleting=False, reason=None,
        owner=("ReplicaSet", "web-7d9f")):
    containers = containers or [ctr()]
    status = {"phase": phase, "containerStatuses": containers}
    spec = {"nodeName": "node-1", "containers": [{"name": c["name"]} for c in containers]}
    if init:
        status["initContainerStatuses"], spec["initContainers"] = list(init), [{"name": c["name"]} for c in init]
    if reason:
        status["reason"] = reason
    meta = {"name": name, "namespace": ns, "creationTimestamp": ts(minutes), "resourceVersion": "1",
            "ownerReferences": [{"kind": owner[0], "name": owner[1]}] if owner else []}
    if deleting:  # (each change a new resourceVersion, as the API server's: the watch tells changes by it; compared
        meta["deletionTimestamp"], meta["resourceVersion"] = ts(0), "2"  # whole, two processes' clocks differed)
    return {"metadata": meta, "spec": spec, "status": status}


PODS = [
    pod("web-7d9f-abcde", "shop", 5),
    pod("web-7d9f-fghij", "shop", 3 * 24 * 60, containers=[ctr(restarts=2, restarted=20)]),  # (ready again lately)
    pod("worker-5c4b-klmno", "jobs", 90, containers=[ctr(ready=False, restarts=7, waiting="CrashLoopBackOff")],
        owner=("ReplicaSet", "worker-5c4b")),
    pod("batch-1-xyz", "jobs", 30, phase="Succeeded", containers=[ctr(ready=False, exited=("Completed", 0))],
        owner=("Job", "batch-1")),
    pod("old-web-pqrst", "shop", 600, deleting=True),
    pod("evicted-uvwxy", "jobs", 2000, phase="Failed", reason="Evicted",
        containers=[ctr(ready=False, exited=("ContainerStatusUnknown", 137))]),
    pod("api-6f7c-mnopq", "shop", 2, containers=[ctr("api", ready=False), ctr("proxy")],  # (starting: api isn't ready)
        init=[ctr("migrate", ready=False, exited=("Completed", 0))], owner=("ReplicaSet", "api-6f7c")),
]
METRICS = {"web-7d9f-abcde": ("250m", "128Mi"), "web-7d9f-fghij": ("1500000n", "64Mi"),
           "worker-5c4b-klmno": ("2", "1Gi"), "old-web-pqrst": ("10m", "32Mi"), "api-6f7c-mnopq": ("40m", "96Mi")}


def node(name, role="control-plane"):
    return {"metadata": {"name": name, "creationTimestamp": ts(9000),
                         "labels": {f"node-role.kubernetes.io/{role}": "true"}},
            "status": {"nodeInfo": {"kubeletVersion": "v1.35.5"},
                       "addresses": [{"type": "InternalIP", "address": "10.0.0.2"}],
                       "conditions": [{"type": "Ready", "status": "True"}]}}
SECRETS = [{"metadata": {"name": "db-creds", "namespace": "shop", "creationTimestamp": ts(100)},
            "type": "Opaque", "data": {"password": "c2VjcmV0LXZhbHVlLURPLU5PVC1MRUFL", "user": "YWRtaW4="}}]
OBJECTS = {
    "namespaces": [{"metadata": {"name": n, "creationTimestamp": ts(5000)}, "status": {"phase": "Active"}}
                   for n in ("default", "jobs", "shop")],
    "deployments": [{"metadata": {"name": "web", "namespace": "shop", "creationTimestamp": ts(4000)},
                     "spec": {"replicas": 2}, "status": {"readyReplicas": 2, "updatedReplicas": 2, "availableReplicas": 2,
                                                         "conditions": [{"type": "Available", "status": "True"}]}}],
    "statefulsets": [{"metadata": {"name": "db", "namespace": "shop", "creationTimestamp": ts(4000)},
                      "spec": {"replicas": 1}, "status": {}}],
    "cronjobs": [{"metadata": {"name": "web", "namespace": "jobs", "creationTimestamp": ts(4000)},
                  "spec": {"suspend": True}, "status": {}}],
    # (what the pods' owners are owned by in turn: web's Deployment; batch-1's CronJob; api-6f7c by nothing)
    "replicasets": [{"metadata": {"name": n, "namespace": "shop", "creationTimestamp": ts(4000),
                                  "ownerReferences": [{"kind": "Deployment", "name": "web", "controller": True}]
                                  if n == "web-7d9f" else []}, "spec": {"replicas": 2}, "status": {}}
                    for n in ("web-7d9f", "api-6f7c")],
    "jobs": [{"metadata": {"name": "batch-1", "namespace": "jobs", "creationTimestamp": ts(30),
                           "ownerReferences": [{"kind": "CronJob", "name": "web", "controller": True}]},
              "spec": {"completions": 1}, "status": {"succeeded": 1}}],
    "nodes": [node("node-1")],
    # a custom resource whose definition declares printer columns (what kubectl get prints): a condition's status
    # by a JSONPath filter, a date, a list's items, an -o wide one (priority 1); its older version prints others
    "customresourcedefinitions": [{
        "metadata": {"name": "widgets.example.com", "creationTimestamp": ts(800)},
        "spec": {"group": "example.com", "scope": "Namespaced", "names": {"kind": "Widget", "plural": "widgets"},
                 "versions": [
                     {"name": "v1beta1", "served": True, "storage": False,
                      "additionalPrinterColumns": [{"name": "Old", "type": "string", "jsonPath": ".spec.old"}]},
                     {"name": "v1", "served": True, "storage": True, "additionalPrinterColumns": [
                         {"name": "Color", "type": "string", "jsonPath": ".spec.color"},
                         {"name": "Size", "type": "integer", "jsonPath": ".spec.size"},
                         {"name": "Parts", "type": "string", "jsonPath": ".spec.parts[*].name"},
                         {"name": "FirstPart", "type": "string", "jsonPath": ".spec.parts[0].name"},
                         {"name": "Ready", "type": "string",
                          "jsonPath": '.status.conditions[?(@.type=="Ready")].status'},
                         {"name": "Reason", "type": "string",
                          "jsonPath": '.status.conditions[?(@.type=="Ready")].reason'},
                         {"name": "Painted", "type": "date", "jsonPath": ".status.lastPainted"},
                         {"name": "Detail", "type": "string", "jsonPath": ".status.detail", "priority": 1},
                         {"name": "Age", "type": "date", "jsonPath": ".metadata.creationTimestamp"}]}]}}],
    "widgets.example.com": [
        {"metadata": {"name": "blue", "namespace": "shop", "creationTimestamp": ts(70)},
         "spec": {"color": "blue", "size": 9, "parts": [{"name": "wheel"}, {"name": "axle"}]},
         "status": {"lastPainted": ts(125), "detail": "only kubectl get -o wide says this",
                    "conditions": [{"type": "Painted", "status": "True", "reason": "Brushed"},
                                   {"type": "Ready", "status": "True", "reason": "Painted"}]}},
        {"metadata": {"name": "red", "namespace": "jobs", "creationTimestamp": ts(20)},
         "spec": {"color": "red", "size": 12, "parts": [{"name": "frame"}]},
         "status": {"conditions": [{"type": "Ready", "status": "False", "reason": "OutOfPaint"}]}}],
}
RESOURCES = ["pods", "namespaces", "nodes", "secrets", "configmaps", "services", "events", "deployments.apps",
             "customresourcedefinitions.apiextensions.k8s.io", "widgets.example.com"]


# KEDA, installed on prod only: its definitions with the printer columns KEDA 2.16 declares, scaled objects in each
# state (ready and active; ready and idle; failing, on its fallback; paused), a scaled job, trigger authentications
def keda_crd(kind, plural, scope, columns):
    return {"metadata": {"name": f"{plural}.keda.sh", "creationTimestamp": ts(30000)},
            "spec": {"group": "keda.sh", "scope": scope, "names": {"kind": kind, "plural": plural},
                     "versions": [{"name": "v1alpha1", "served": True, "storage": True,
                                   "additionalPrinterColumns": columns}]}}


def cond(kind):
    return {"name": kind, "type": "string", "jsonPath": f'.status.conditions[?(@.type=="{kind}")].status'}


KEDA_AGE = {"name": "Age", "type": "date", "jsonPath": ".metadata.creationTimestamp"}
KEDA_TRIGGERS = [{"name": "Triggers", "type": "string", "jsonPath": ".status.triggersTypes"},
                 {"name": "Authentications", "type": "string", "jsonPath": ".status.authenticationsTypes"}]
KEDA_AUTH_COLUMNS = [
    {"name": "PodIdentity", "type": "string", "jsonPath": ".spec.podIdentity.provider"},
    {"name": "Secret", "type": "string", "jsonPath": ".spec.secretTargetRef[*].name"},
    {"name": "Env", "type": "string", "jsonPath": ".spec.env[*].name"},
    {"name": "VaultAddress", "type": "string", "jsonPath": ".spec.hashiCorpVault.address"},
    {"name": "ScaledObjects", "type": "string", "jsonPath": ".status.scaledobjects", "priority": 1},
    {"name": "ScaledJobs", "type": "string", "jsonPath": ".status.scaledjobs", "priority": 1}]
KEDA_CRDS = [
    keda_crd("ScaledObject", "scaledobjects", "Namespaced", [
        {"name": "ScaleTargetKind", "type": "string", "jsonPath": ".status.scaleTargetKind"},
        {"name": "ScaleTargetName", "type": "string", "jsonPath": ".spec.scaleTargetRef.name"},
        {"name": "Min", "type": "integer", "jsonPath": ".spec.minReplicaCount"},
        {"name": "Max", "type": "integer", "jsonPath": ".spec.maxReplicaCount"},
        cond("Ready"), cond("Active"), cond("Fallback"), cond("Paused"), *KEDA_TRIGGERS, KEDA_AGE]),
    keda_crd("ScaledJob", "scaledjobs", "Namespaced", [
        {"name": "Min", "type": "integer", "jsonPath": ".spec.minReplicaCount"},
        {"name": "Max", "type": "integer", "jsonPath": ".spec.maxReplicaCount"},
        cond("Ready"), cond("Active"), cond("Paused"), *KEDA_TRIGGERS, KEDA_AGE]),
    keda_crd("TriggerAuthentication", "triggerauthentications", "Namespaced", KEDA_AUTH_COLUMNS),
    keda_crd("ClusterTriggerAuthentication", "clustertriggerauthentications", "Cluster", KEDA_AUTH_COLUMNS)]


def scaled(name, ns, target, low, high, triggers, minutes, ready="True", active="True", fallback="False",
           paused="False", auth=None, kind="apps/v1.Deployment", why=None):
    """A ScaledObject as KEDA keeps it: its spec, and the status and conditions its operator writes."""
    meta = {"name": name, "namespace": ns, "creationTimestamp": ts(minutes)}
    if paused == "True":
        meta["annotations"] = {"autoscaling.keda.sh/paused": "true"}
    reasons = {"Ready": why or ("ScaledObjectReady" if ready == "True" else "ScaledObjectCheckFailed"),
               "Active": "ScalerActive" if active == "True" else "ScalerNotActive",
               "Fallback": "FallbackExists" if fallback == "True" else "NoFallbackFound",
               "Paused": "ScaledObjectPaused" if paused == "True" else "ScaledObjectUnpaused"}
    states = {"Ready": ready, "Active": active, "Fallback": fallback, "Paused": paused}
    return {"apiVersion": "keda.sh/v1alpha1", "kind": "ScaledObject", "metadata": meta,
            "spec": {"scaleTargetRef": {"name": target}, "minReplicaCount": low, "maxReplicaCount": high,
                     "triggers": [{"type": t, **({"authenticationRef": {"name": auth}} if auth else {})}
                                  for t in triggers]},
            "status": {"scaleTargetKind": kind, "triggersTypes": ",".join(triggers),
                       **({"authenticationsTypes": auth} if auth else {}),
                       "conditions": [{"type": k, "status": v, "reason": reasons[k]} for k, v in states.items()]}}


PROD_OBJECTS = {
    "customresourcedefinitions": KEDA_CRDS,
    "scaledobjects.keda.sh": [
        scaled("orders-api", "shop", "orders-api", 2, 20, ["prometheus", "cpu"], 3 * 24 * 60, auth="prometheus-auth"),
        scaled("reports-worker", "jobs", "reports-worker", 0, 5, ["rabbitmq"], 2 * 24 * 60, active="False",
               auth="rabbitmq-auth"),
        scaled("payments-consumer", "shop", "payments-consumer", 1, 10, ["kafka"], 40, ready="False",
               active="False", fallback="True"),
        scaled("batch-ingest", "jobs", "ingest", 1, 3, ["cron"], 9 * 24 * 60, active="False", paused="True",
               kind="apps/v1.StatefulSet")],
    "scaledjobs.keda.sh": [{
        "apiVersion": "keda.sh/v1alpha1", "kind": "ScaledJob",
        "metadata": {"name": "video-transcode", "namespace": "jobs", "creationTimestamp": ts(600)},
        "spec": {"maxReplicaCount": 8, "jobTargetRef": {"template": {}},
                 "triggers": [{"type": "aws-sqs-queue", "authenticationRef": {"name": "aws-credentials",
                                                                             "kind": "ClusterTriggerAuthentication"}}]},
        "status": {"triggersTypes": "aws-sqs-queue", "authenticationsTypes": "aws-credentials",
                   "conditions": [{"type": "Ready", "status": "True"}, {"type": "Active", "status": "True"},
                                  {"type": "Paused", "status": "False"}]}}],
    "triggerauthentications.keda.sh": [
        {"metadata": {"name": "rabbitmq-auth", "namespace": "jobs", "creationTimestamp": ts(3000)},
         "spec": {"secretTargetRef": [{"parameter": "host", "name": "rabbitmq-conn", "key": "url"},
                                      {"parameter": "password", "name": "rabbitmq-pass", "key": "password"}]},
         "status": {"scaledobjects": "reports-worker"}},
        {"metadata": {"name": "prometheus-auth", "namespace": "shop", "creationTimestamp": ts(3000)},
         "spec": {"env": [{"parameter": "bearerToken", "name": "PROM_TOKEN"}]},
         "status": {"scaledobjects": "orders-api"}}],
    "clustertriggerauthentications.keda.sh": [
        {"metadata": {"name": "aws-credentials", "creationTimestamp": ts(3000)},
         "spec": {"podIdentity": {"provider": "aws"}}, "status": {"scaledjobs": "video-transcode"}}],
}
PROD_RESOURCES = ["scaledobjects.keda.sh", "scaledjobs.keda.sh", "triggerauthentications.keda.sh",
                  "clustertriggerauthentications.keda.sh"]


def out(text, code=0):
    sys.stdout.write(text)
    sys.exit(code)


def fail(text, code=1):
    sys.stderr.write(text + "\n")
    sys.exit(code)


# what each container writes, a line a request; a crashed one's previous run ends in its error
LOG_LINES = ["INFO  starting {c} in pod {p} (pid 1)", "INFO  config loaded from /etc/{c}/config.yaml (env: dev)",
             "INFO  connected to db:5432 (pool: 10)", "INFO  listening on :8080", "INFO  GET /healthz 200 0.8ms",
             "INFO  GET /api/orders?status=open 200 14.2ms", "INFO  POST /api/orders 201 22.7ms (order 1042)",
             "WARN  slow query: SELECT * FROM orders WHERE status = 'open' ORDER BY created_at DESC (412ms)",
             "INFO  GET /api/orders/1042 200 3.9ms", "INFO  GET /healthz 200 0.7ms",
             "ERROR POST /api/payments 502 30012ms: upstream payments:9000 timed out after 30s",
             "INFO  retrying payment for order 1042 (attempt 2 of 3)", "INFO  POST /api/payments 200 182.4ms",
             "INFO  GET /healthz 200 0.9ms"]
PREVIOUS_LINES = ["INFO  starting {c} in pod {p} (pid 1)", "ERROR connection to db:5432 refused",
                  "FATAL giving up after 5 tries"]


def deleted():
    """The pods deleted so far ($FAKE_KUBECTL_STATE): {"namespace/name": "force" or "grace"}."""
    path = os.environ.get("FAKE_KUBECTL_STATE")
    if not path or not os.path.exists(path):
        return {}
    with open(path) as f:
        return json.load(f)


def logs(rest):
    """kubectl logs [-f | --previous] --tail=N -n NS POD -c CONTAINER"""
    ns, container = rest[rest.index("-n") + 1], rest[rest.index("-c") + 1]
    name = next(a for i, a in enumerate(rest[1:], 1) if not a.startswith("-") and rest[i - 1] not in ("-n", "-c"))
    hit = next((p for p in PODS if (p["metadata"]["namespace"], p["metadata"]["name"]) == (ns, name)), None)
    if hit is None:
        fail(f'Error from server (NotFound): pods "{name}" not found')
    spec = hit["spec"]
    if container not in [c["name"] for c in spec.get("initContainers", []) + spec["containers"]]:
        fail(f"error: container {container} is not valid for pod {name}")
    if "--previous" in rest:
        statuses = hit["status"].get("containerStatuses", []) + hit["status"].get("initContainerStatuses", [])
        if not any(c["name"] == container and c.get("restartCount") for c in statuses):
            fail(f'Error from server (BadRequest): previous terminated container "{container}" in pod "{name}" '
                 'not found')
        lines = PREVIOUS_LINES
    else:
        lines = LOG_LINES
    for i, line in enumerate(lines):
        when = f"{NOW[:11]}09:{i * 7 // 60:02d}:{i * 7 % 60:02d}.{i * 137 % 1000:03d}Z"
        print(f"{when} {line.format(c=container, p=name)}", flush=True)
    if "-f" in rest and "--previous" not in rest:
        every, n, end = float(os.environ.get("FAKE_KUBECTL_FOLLOW") or 0), len(lines), time.time() + 120
        while time.time() < end:  # (stopped by the view; never left running long, should a test not)
            time.sleep(every or 0.5)
            if every:
                n += 1
                print(f"{NOW[:11]}10:00:00.000Z INFO  GET /api/orders/{n} 200 3.1ms", flush=True)
    sys.exit(0)


def delete(rest):
    """kubectl delete pod NAME -n NS (--wait=false | --grace-period=0 --force)"""
    kind, name, ns = rest[1], rest[2], rest[rest.index("-n") + 1]
    if kind != "pod" or not any((p["metadata"]["namespace"], p["metadata"]["name"]) == (ns, name) for p in PODS) or \
            deleted().get(f"{ns}/{name}") == "force":
        fail(f'Error from server (NotFound): pods "{name}" not found')
    force = "--force" in rest and "--grace-period=0" in rest
    if path := os.environ.get("FAKE_KUBECTL_STATE"):
        state = deleted()
        state[f"{ns}/{name}"] = "force" if force else "grace"
        with open(path, "w") as f:
            json.dump(state, f)
    if force:
        sys.stderr.write("Warning: Immediate deletion does not wait for confirmation that the running resource has "
                         "been terminated. The resource may continue to run on the cluster indefinitely.\n")
        out(f'pod "{name}" force deleted\n')
    out(f'pod "{name}" deleted\n')


def current(resource, context="k3d-dev"):
    """A kind's objects now: a deleted pod Terminating, or not there at all if it was forced; KEDA's on prod."""
    items = PODS if resource == "pods" else OBJECTS.get(resource, []) + \
        (PROD_OBJECTS.get(resource, []) if context == "prod" else [])
    if resource == "pods" and (gone := deleted()):
        items = [p if f"{p['metadata']['namespace']}/{p['metadata']['name']}" not in gone else
                 {**p, "metadata": {**p["metadata"], "deletionTimestamp": ts(0), "resourceVersion": "2"}} for p in items
                 if gone.get(f"{p['metadata']['namespace']}/{p['metadata']['name']}") != "force"]
    return items


def on_context(items, resource, context):
    if context == "prod" and resource == "pods":
        return items[:2]
    if context == "prod" and resource == "nodes":
        return [node("prod-1")] + [node(f"prod-{i}", "worker") for i in (2, 3)]
    return items


def watch(resource, context):
    """kubectl get KIND --watch --output-watch-events -o json: what's there as ADDED, then each change as it happens
    (a pod deleted: MODIFIED, Terminating, or DELETED if forced), each event indented JSON as kubectl writes it; for
    $FAKE_KUBECTL_WATCH_S seconds (two minutes by default), then it ends, as the API server ends a watch."""
    def key(o):
        return o["metadata"].get("namespace", ""), o["metadata"]["name"]

    def emit(kind, o):
        sys.stdout.write(json.dumps({"type": kind, "object": o}, indent=4) + "\n")
        sys.stdout.flush()
    shown, end = {}, time.time() + float(os.environ.get("FAKE_KUBECTL_WATCH_S") or 120)
    while True:
        now = {key(o): o for o in on_context(current(resource, context), resource, context)}
        for k, o in now.items():
            if k not in shown or json.dumps(o, sort_keys=True) != json.dumps(shown[k], sort_keys=True):
                emit("MODIFIED" if k in shown else "ADDED", o)
        for k in shown.keys() - now.keys():
            emit("DELETED", shown[k])
        shown = now
        if time.time() > end:
            sys.exit(0)
        time.sleep(0.2)


def main():
    args = sys.argv[1:]
    with open(os.environ.get("FAKE_KUBECTL_LOG", os.devnull), "a") as f:
        f.write(json.dumps([os.path.basename(sys.argv[0])] + args) + "\n")
    if args[:1] == ["list"]:  # (helm list)
        out(json.dumps([{"name": "web", "namespace": "shop", "revision": "3", "updated": "2026-10-22 10:00:00",
                         "status": "deployed", "chart": "web-1.2.0", "app_version": "1.2.0"}]))
    context = args[args.index("--context") + 1] if "--context" in args else "k3d-dev"
    if context not in ("k3d-dev", "prod"):
        sys.stderr.write(f'error: context "{context}" does not exist\n')
        sys.exit(1)
    # a test's folder ($FAKE_KUBECTL_HOLD): a file named after the context holds its answers back while it's there;
    # one named <context>.down makes it a cluster that doesn't answer at all
    if hold := os.environ.get("FAKE_KUBECTL_HOLD"):
        while os.path.exists(os.path.join(hold, context)):
            time.sleep(0.02)
        if os.path.exists(os.path.join(hold, context + ".down")):
            sys.stderr.write("Unable to connect to the server: dial tcp 127.0.0.1:6443: connect: connection refused\n")
            sys.exit(1)
    rest = [a for i, a in enumerate(args) if a != "--context" and (i == 0 or args[i - 1] != "--context")]
    if rest[:1] == ["api-resources"]:
        out("\n".join(RESOURCES + PROD_RESOURCES * (context == "prod")) + "\n")
    if rest[:1] == ["exec"] and "arangosh" in " ".join(rest):  # (arangosh in an ArangoDB pod)
        script, program = rest[-3], rest[-1]
        if '"$ARANGO_ROOT_PASSWORD"' not in script or "@@" not in program and "@" in program.replace("@@", ""):
            sys.stderr.write("fake kubectl: arangosh without the container's own password, or with @s not doubled\n")
            sys.exit(4)
        js = program.replace("@@", "@")
        if "out[n] = db._collections()" in js:
            out(json.dumps({"_system": [], "kg_test": ["e_Job", "e_Customer", "e_Note", "r_JOB_FOR_CUSTOMER",
                                                       "r_NOTE_ABOUT_CUSTOMER"]}))
        if "var DATABASE" in js and 'var DATABASE = "kg_test"' not in js:
            out(json.dumps({"tables": [], "links": [], "graphs": []}))
        if "var DATABASE" in js:
            def attrs(*pairs):
                return [{"name": n, "type": t, "k": k} for n, t, k in pairs]
            out(json.dumps({"tables": [
                {"name": "e_Customer", "edge": False, "count": 1, "sampled": 1,
                 "attrs": attrs(("id", "string", 1), ("name", "string", 1), ("password_hash", "string", 1))},
                {"name": "e_Job", "edge": False, "count": 2, "sampled": 2,
                 "attrs": attrs(("id", "string", 2), ("title", "string", 2), ("notes", "null", 1), ("notes", "string", 1))},
                {"name": "e_Note", "edge": False, "count": 0, "sampled": 0, "attrs": []},
                {"name": "r_JOB_FOR_CUSTOMER", "edge": False, "count": 2, "sampled": 2,
                 "attrs": attrs(("from_id", "string", 2), ("to_id", "string", 2))},
                {"name": "r_NOTE_ABOUT_CUSTOMER", "edge": False, "count": 0, "sampled": 0, "attrs": []}],
                "links": [{"rel": "r_JOB_FOR_CUSTOMER", "from_attr": "from_id", "to_attr": "to_id", "from": "e_Job",
                           "to": "e_Customer"},
                          {"rel": "r_NOTE_ABOUT_CUSTOMER", "from_attr": "from_id", "to_attr": "to_id", "from": None,
                           "to": None}], "graphs": []}))
        if "RETURN UNSET(d" in js:
            out(json.dumps([{"_key": "c1", "id": "c1", "name": "Acme", "password_hash": "not-for-your-eyes",
                             "tags": ["a", "b"]}]))
        out(json.dumps(["_system", "kg_test"]))
    if rest[:1] == ["exec"]:  # (psql in a Postgres pod: the databases, a database's tables or schema)
        sql = sys.stdin.read()
        db = rest[rest.index("-d") + 1] if "-d" in rest else ""
        if not sql.lstrip().lower().startswith("select"):
            sys.stderr.write("fake kubectl: psql asked for more than a read\n")
            sys.exit(5)
        if "pg_database" in sql:
            out("postgres\nrelay\n")
        if sql.startswith("select table_name from information_schema.tables"):
            out({"relay": "relay_gen_job\n"}.get(db, ""))
        if db != "relay":
            out(json.dumps({"tables": [], "columns": [], "pks": [], "fks": []}))
        out(json.dumps({"tables": [{"s": "public", "t": "relay_gen_job", "rows": 3, "kind": "r"}],
                        "columns": [{"s": "public", "t": "relay_gen_job", "c": c, "type": t, "null": False, "def": None}
                                    for c, t in (("job_id", "text"), ("preview", "jsonb"))],
                        "pks": [{"s": "public", "t": "relay_gen_job", "c": "job_id"}], "fks": []}))
    if rest[:1] == ["logs"]:
        logs(rest)
    if rest[:1] == ["delete"]:
        delete(rest)
    if rest[:1] != ["get"]:
        sys.stderr.write(f"fake kubectl: only get, api-resources, logs and delete here, not {rest[:1]}\n")
        sys.exit(2)
    rest = rest[1:]
    if rest[:1] == ["--raw"]:
        if context == "prod":
            sys.stderr.write("Error from server (NotFound): the server could not find the requested resource\n")
            sys.exit(1)
        if rest[1] in ("/apis/apps/v1/deployments", "/apis/apps/v1/statefulsets", "/apis/apps/v1/daemonsets",
                       "/apis/batch/v1/cronjobs"):  # (a kind's list, every namespace: items carry no kind)
            out(json.dumps({"kind": "List", "items": OBJECTS.get(rest[1].rsplit("/", 1)[1], [])}))
        if rest[1].endswith("/pods"):
            out(json.dumps({"items": [{"metadata": {"name": n, "namespace": p["metadata"]["namespace"]},
                                       "containers": [{"name": "main", "usage": {"cpu": c, "memory": m}}]}
                                      for n, (c, m) in METRICS.items()
                                      for p in PODS if p["metadata"]["name"] == n]}))
        out(json.dumps({"items": [{"metadata": {"name": "node-1"}, "usage": {"cpu": "500m", "memory": "2Gi"}}]}))
    resource = rest[0]
    fmt = next((a for a in rest if a.startswith("go-template=")), None) or \
        (rest[rest.index("-o") + 1] if "-o" in rest else "")
    if resource == "secrets":
        if not fmt.startswith("go-template=") or "{{.data}}" in fmt or "$v}}" in fmt:
            sys.stderr.write("fake kubectl: a Secret's values were asked for\n")
            sys.exit(3)
        out("".join(f"{s['metadata']['namespace']}\t{s['metadata']['name']}\t{s['type']}\t"
                    f"{s['metadata']['creationTimestamp']}\t{''.join(k + ',' for k in s['data'])}\n" for s in SECRETS))
    if resource in PROD_RESOURCES and context != "prod":  # (no KEDA here)
        fail(f'error: the server doesn\'t have a resource type "{resource.split(".")[0]}"')
    items = current(resource, context)
    if "--watch" in rest:
        watch(resource, context)
    if resource == "pods" and os.environ.get("FAKE_KUBECTL_PG"):  # (a cluster with Postgres in a pod)
        items = items + [{"metadata": {"name": "postgres-0", "namespace": "data", "creationTimestamp": ts(500)},
                          "spec": {"containers": [{"name": "db", "image": "docker.io/library/postgres:18.1",
                                                   "env": [{"name": "POSTGRES_USER", "value": "app"},
                                                           {"name": "POSTGRES_PASSWORD",
                                                            "valueFrom": {"secretKeyRef": {"name": "pg"}}}]}]},
                          "status": {"phase": "Running"}},
                         {"metadata": {"name": "arangodb-0", "namespace": "data", "creationTimestamp": ts(500)},
                          "spec": {"containers": [{"name": "arangodb", "image": "arangodb:3.8",
                                                   "env": [{"name": "ARANGO_ROOT_PASSWORD",
                                                            "valueFrom": {"secretKeyRef": {"name": "arango"}}}]}]},
                          "status": {"phase": "Running"}},
                         {"metadata": {"name": "cnpg-operator-1", "namespace": "cnpg-system"},
                          "spec": {"containers": [{"name": "manager",
                                                   "image": "ghcr.io/cloudnative-pg/cloudnative-pg:1.24.1"}]},
                          "status": {"phase": "Running"}}]
    if len(rest) > 1 and not rest[1].startswith("-"):  # (one object: its YAML)
        name = rest[1]
        hit = next((o for o in items if o["metadata"]["name"] == name), None)
        if hit is None:
            sys.stderr.write(f'Error from server (NotFound): {resource} "{name}" not found\n')
            sys.exit(1)
        if fmt == "json":
            out(json.dumps(hit))
        out(f"apiVersion: v1\nkind: Object\nmetadata:\n  name: {name}\n  namespace: "
            f"{hit['metadata'].get('namespace', '')}\nstatus:\n  phase: {hit.get('status', {}).get('phase', '')}\n")
    items = on_context(items, resource, context)
    if fmt == "name":  # (node/node-1)
        out("".join(f"{resource[:-1]}/{o['metadata']['name']}\n" for o in items))
    out(json.dumps({"apiVersion": "v1", "kind": "List", "items": items}))


main()
