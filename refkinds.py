"""refkinds — what a Signal ref is and how a label shows it: its kind and its span in text (ref_kind, ref_span), the
command that reads it (ref_command; Kubernetes' kinds and ref_kube_command), a cloud provider's, a pull request's
pill (pr_pill_markup), and LinkLabel, the label that draws a ref's logo, a chip's patch and a pill behind their
names (chip_markup).
"""
import html
import json
import os
import re

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Gdk, Graphene, Gsk, Gtk, Pango  # noqa: E402,F401

from lookup import ref_key  # noqa: E402,F401
from lookupkube import _KUBE_NAME, _kube_args  # noqa: E402,F401
from baudelib.transcripts import ref_epoch, typed_at  # noqa: E402
from widgets import (  # noqa: E402
    PR_COLORS, PR_SAID, WrapLabel, accent_rgba, accent_text_rgba, code_scale, draw_ref_icon, screen_scale,
)


# -------------------------------------------------------------- signal --
# Signal: a second pass over Claude's finished replies that works out where the
# things they name live (files, commits, branches, PRs, env vars, constants, HTTP routes) and keeps the
# answers in <the chat's folder>/.baude/signal.db, so those names become links. Every location keeps an
# anchor (the file's blob hash, its git state, the lines around it), so it can be found again after the code
# changes under it. A name it can't find is shown as not found, with a Refresh to search again.

REF_SCHEME = "baude-ref:"


_REF_METHODS = "GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS"


_REF_MORE_EXT = "lock|txt|log|csv|png|jpe?g|gif|webp|pdf|xml|tpl|j2|mako|dockerfile|env|example|sample|bak"


_REF_EXT = "py|pyi|ts|tsx|js|jsx|mjs|cjs|rs|go|java|kt|rb|sh|bash|ya?ml|toml|json|md|css|scss|html|sql|tf|tsl|tsp|mmd|svg|ini|cfg|conf"


# names worth looking up in plain text (outside backticks): only shapes that are rarely ordinary words
_REF_PLAIN = re.compile(
    r"(?<![\w/.@`$-])(?:"
    r"(?P<symbol>[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*\(\))"
    rf"|(?P<route>(?:{_REF_METHODS})\s+(?:…|\.\.\.)?/[\w./{{}}<>:~*-]*[\w}}>*-])"
    r"|(?P<pr>(?:PR|pull request) #\d+|[\w.-]+/[\w.-]+#\d+)"
    r"|(?P<env>[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+)"
    r"|(?P<commit>[0-9a-f]{7,40})"
    rf"|(?P<file>(?:[\w.@+-]+/)*[\w@+-][\w.@+-]*\.(?:{_REF_EXT})(?::\d+(?::\d+)?)?)"
    r")(?![\w/-])")


def ref_kind(text):
    """What a name may be (a guess the resolvers settle), or None if it doesn't look like one."""
    t = ref_key(text)
    if not t or len(t) > 200 or "\n" in t:
        return None
    if re.fullmatch(rf"(?:{_REF_METHODS})\s+(?:…|\.\.\.)?/\S*", t) or \
            re.fullmatch(r"(?:…|\.\.\.)/[\w.{}<>:~*-]+(?:/[\w.{}<>:~*-]*)*", t) or \
            re.fullmatch(r"/(?:v\d+|api|private|internal|auth|__[\w-]+)(?:/[\w.{}<>:~*-]*)*(?:…|\.\.\.)?", t):
        return "route"  # (…/jobs/{job}/retry: the end of one; /v1/private/jobs/…: every route under it)
    if ref_hashlike(t) or ref_commit_range(t):
        return "commit"
    if re.fullmatch(r"[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+", t):
        return "env"
    if re.fullmatch(r"(?:(?:PR|pull request) )?#\d+|[\w.-]+/[\w.-]+#\d+", t, re.I):
        return "pr"
    if re.fullmatch(r"--[A-Za-z][\w-]*", t):
        return "cli"  # a flag: where it's defined
    if kube := ref_kube(t):  # (deploy/x, a kebab-case name, .Values.image.tag, spec.strategy.type: ref_kube)
        return "config" if kube[0] in ("values", "field") else "k8s"
    if " " in t:  # a call with its arguments, or a span ref_span reads (an object's shape, code), or a command
        if re.match(r"(?:[\w./~-]*/)?(?:kubectl|kubectx|oc|helm)\s", t):  # (-l app=x, --set k=v: still a command)
            return "cli" if ref_command(t) else None
        return "symbol" if ("(" in t and ref_symbolish(t)) or ref_span(t) else "cli" if ref_command(t) else None
    path = re.sub(r":\d+(?::\d+)?$", "", t)
    if path.startswith("@"):  # an npm scope (@orchard/ui): a package, not a path
        return None
    if "/" in path:
        if not re.fullmatch(r"[\w.@+~/-]+", path):
            return None
        last = path.rstrip("/").rsplit("/", 1)[-1]
        if not path.endswith("/") and not path.startswith(("/", "~", ".")) and \
                not re.search(r"\.[A-Za-z][A-Za-z0-9]{0,7}$", last):
            return "branch"  # dev/release-merge, origin/staging; else a folder (vendor/orchard-ui)
        return "file"
    if re.fullmatch(rf"[\w@+-][\w.@+-]*\.(?:{_REF_EXT}|{_REF_MORE_EXT})", path, re.I):
        return "file"  # a known extension: ui.fill and runs.unpack are code, not files
    return "symbol" if ref_symbolish(t) or ref_span(t) else None


def ref_symbolish(t):
    """Whether a name looks like code: a call (installs(), _install(..., upgrade=True)), a dotted name
    (quick_edit.start, previous.job_id, bundle.apps[].installed_installation_id), snake_case, camelCase, or a
    PascalCase compound (EditPane). Plain words (done, target, Generation) aren't: too many are ordinary words."""
    m = re.fullmatch(r"([A-Za-z_]\w*(?:\[\d*\*?\])?(?:\.[A-Za-z_]\w*(?:\[\d*\*?\])?)*)(\([^()]*\))?", t)
    if not m:
        return False
    name = re.sub(r"\[[^\]]*\]", "", m.group(1))
    return bool(m.group(2)) or "." in name or bool(re.search(r"[a-z0-9]_\w|^_+[a-z]", name)) or \
        bool(re.fullmatch(r"[a-z]+[A-Z]\w*", name)) or bool(re.fullmatch(r"[A-Z][a-z0-9]+[A-Z]\w*", name))


_REF_ID = r"[A-Za-z_]\w*"


_REF_DOTTED = rf"{_REF_ID}(?:\.{_REF_ID})*"


def ref_span(t):
    """A backticked span that's more than one name, as the code-symbol resolver reads it (None if it isn't one).
    The span as a whole is the glossary's entry, so the plain words in it (name, app, target) are looked up only
    together with the rest, never on their own:
      ("shape", key, names): an object's shape, {installation_id, member_id, name} or previous: {job_id, seq}
      ("pair", key, value): a key and a value that tells its definitions apart, preview.reason: crashed
      ("query", name, value): a URL's query parameter, ?orchard_theme=dark
      ("excerpt", text, None): code as it's written, keep_dist=package == "orchard-ui" (linked only if it is)"""
    t = ref_key(t)
    if ref_symbolish(t):
        return None
    if m := re.fullmatch(rf"(?:({_REF_DOTTED})\s*[:=]\s*)?\[?\{{\s*({_REF_ID}\??(?:\s*,\s*{_REF_ID}\??)+)"
                         r"(?:\s*,\s*(?:\.\.\.|…))?\s*\}\]?", t):
        names = [n.strip().rstrip("?") for n in m.group(2).split(",")]
        return ("shape", m.group(1), names) if len(set(names)) == len(names) else None
    if m := re.fullmatch(rf"([a-z_]\w*(?:\.{_REF_ID})*):\s+([\"']?)([\w.-]+)\2", t):  # (Error: ENOENT is a log line)
        value = m.group(3)  # (a word or a long number: true, 1 or 0 tell no definitions apart)
        if re.search(r"[A-Za-z]{2}", value) and value.lower() not in ("true", "false", "null", "none", "yes", "no") \
                or re.fullmatch(r"\d{3,}", value):
            return "pair", m.group(1), value
        return None
    if m := re.fullmatch(r"\?(\w+)=([^&\s]*)(?:&\w+=[^&\s]*)*", t):
        return "query", m.group(1), m.group(2)
    if re.search(r"[A-Za-z_]\w", t) and re.search(r"[=\[\"'(]|->", t) and "://" not in t and \
            not re.match(rf"(?:-|\$|(?:{_REF_METHODS})\s)", t) and len(re.sub(r"\.\.\.|…|\W", "", t)) >= 3:
        return "excerpt", t, None
    return None


def ref_command(t):
    """A command as replies write one, read for what it runs (None if it isn't one Signal can place):
      ("module", "pkg.mod"): python -m pkg.mod ...
      ("file", "scripts/shot.mjs"): node scripts/shot.mjs, build.mjs --check, vite --config web/vite.config.ts:
        the first file it names (the script, else the file it's given)
      ("flag", "--set"): a flag with its value, --set imageTag=$SHA
      ("flag", "--check", "mytool"): a program's flag, mytool --check: where it's defined, in that program
      ("kube", ...): what a kubectl or helm command acts on (ref_kube_command)"""
    t = ref_key(t)
    words = t.split()
    while words and re.fullmatch(r"[A-Z_][A-Z0-9_]*=\S*", words[0]):  # (FOO=1 node x.js)
        words = words[1:]
    if len(words) < 2 or len(t) > 160 or not re.fullmatch(r"[\w./~-]+", words[0]):
        return None
    if os.path.basename(words[0]) in ("kubectl", "kubectx", "oc", "helm"):
        return ref_kube_command(words)
    if re.fullmatch(r"--[A-Za-z][\w-]*", words[0]) and len(words) == 2:
        return "flag", words[0]
    if (i := next((i for i, w in enumerate(words[:-1]) if w == "-m" and i), None)) is not None and \
            re.fullmatch(r"python[\d.]*", os.path.basename(words[i - 1])) and \
            re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*", words[i + 1]):
        return "module", words[i + 1]
    for w in words:
        if w.startswith("-") or "<" in w or "$" in w or "*" in w or "://" in w:
            continue
        w = w.split("=", 1)[-1].strip("'\"")
        if re.fullmatch(rf"[\w./~@+-]*[\w@+-]\.(?:{_REF_EXT})", w, re.I):
            return "file", w
    flag = next((w.split("=", 1)[0] for w in words[1:] if re.fullmatch(r"--[A-Za-z][\w-]*(?:=\S*)?", w)), None)
    if flag and re.fullmatch(r"[a-z][\w.-]*", words[0]):
        return "flag", flag, words[0]
    return None


def ref_quiet(surface):
    """Whether a backticked span is linked only once it's found: a piece of code (ref_span's excerpt), a command or
    flag (most are other programs'), or a bare kebab-case name (a Kubernetes object's, if the repository defines one,
    but as likely a package's or a command's), so not finding it says nothing."""
    return (span := ref_span(surface)) is not None and span[0] == "excerpt" or ref_kind(surface) == "cli" or \
        (kube := ref_kube(surface)) is not None and kube[0] == "name"


def ref_hashlike(t):
    """A commit hash as people write one: 7-12 hex digits, or all 40 (64 in a SHA-256 repository),
    with letters and digits both. A 32-digit id (a UUID without dashes) isn't one."""
    return bool(re.fullmatch(r"[0-9a-f]{7,12}|[0-9a-f]{40}|[0-9a-f]{64}", t)) and bool(re.search(r"[a-f]", t)) \
        and bool(re.search(r"\d", t))


def ref_commit_range(t):
    """A range of commits as git writes one (6207ca2..d9b150f, a...b): (from, "..", to), or None."""
    m = re.fullmatch(r"([0-9a-f]{7,40})(\.\.\.?)([0-9a-f]{7,40})", t)
    return m.groups() if m and ref_hashlike(m[1]) and ref_hashlike(m[3]) else None


def ref_pr_info(p):
    """The parts of a GitHub pull request (REST shape) the cards show."""
    state = "merged" if p.get("merged_at") else p.get("state", "")
    return {"number": p.get("number"), "title": p.get("title", ""), "state": state, "url": p.get("html_url"),
            "created": p.get("created_at"), "merged": p.get("merged_at"), "closed": p.get("closed_at")}


# (ref_epoch and typed_at: baudelib.transcripts, as the headless server reads transcripts too)


def last_typed(path, chunk=262144, limit=32 << 20):
    """When a transcript was last typed in (typed_at), as epoch seconds, read back from its end a chunk at a time: the
    tool results after the last prompt can run to megabytes. None if nothing typed is in its last `limit` bytes."""
    try:
        with open(path, "rb") as f:
            end = f.seek(0, os.SEEK_END)
            carry, read = b"", 0
            while end > 0 and read < limit:
                start = max(0, end - chunk)
                f.seek(start)
                data = f.read(end - start) + carry
                read += end - start
                lines = data.split(b"\n")
                carry = lines.pop(0) if start > 0 else b""  # (cut off at the chunk's start: whole with the next)
                if len(carry) > limit:
                    carry = b""  # (one enormous line: a tool's result, never something typed)
                said = None
                for raw in lines:
                    if b'"user"' not in raw[:4096] and b'"queued_command"' not in raw[:300] \
                            or b'"tool_use_id"' in raw[:1500]:  # (a tool's result: not decoded)
                        continue
                    try:
                        when = typed_at(json.loads(raw))
                    except ValueError:
                        continue
                    if when and when > (said or ""):
                        said = when
                if said:
                    return ref_epoch(said)
                end = start
    except OSError:
        pass
    return None


# ------------------------------------------------------- signal: Kubernetes --
# Replies about a cluster name its objects (`deploy/relay-svc`, "the `beacon-runner` Deployment", a pod
# `relay-svc-7d9f8b6c5-x2x4z`, `relay-svc.relay.svc.cluster.local`), their configuration (`.Values.image.tag`,
# `helm upgrade … --set image.tag=…`, `spec.strategy.type`) and the contexts commands run against
# (`kubectl --context k3d-dev …`). The rules place them from what the repository says, with no cluster: raw
# manifests; Helm charts, a templated name rendered for each release the repository's own `helm upgrade --install`
# lines name, else matched by its literal parts; kustomize overlays (namePrefix / nameSuffix, generated ConfigMaps).
# The kubeconfig gives its contexts' names, clusters and namespaces, never credentials; nothing asks a cluster
# anything (kubectl isn't run).

KUBE_KINDS = {alias: kind for kind, aliases in {
    "Pod": "po pod pods", "Deployment": "deploy deployment deployments", "Service": "svc service services",
    "StatefulSet": "sts statefulset statefulsets", "DaemonSet": "ds daemonset daemonsets",
    "ReplicaSet": "rs replicaset replicasets", "Job": "job jobs", "CronJob": "cj cronjob cronjobs",
    "ConfigMap": "cm configmap configmaps", "Secret": "secret secrets", "Ingress": "ing ingress ingresses",
    "Namespace": "ns namespace namespaces", "ServiceAccount": "sa serviceaccount serviceaccounts",
    "PersistentVolumeClaim": "pvc persistentvolumeclaim persistentvolumeclaims",
    "PersistentVolume": "pv persistentvolume persistentvolumes",
    "HorizontalPodAutoscaler": "hpa horizontalpodautoscaler horizontalpodautoscalers",
    "PodDisruptionBudget": "pdb poddisruptionbudget poddisruptionbudgets",
    "NetworkPolicy": "netpol networkpolicy networkpolicies", "Role": "role roles", "RoleBinding": "rolebinding rolebindings",
    "ClusterRole": "clusterrole clusterroles", "ClusterRoleBinding": "clusterrolebinding clusterrolebindings",
    "StorageClass": "sc storageclass storageclasses", "Endpoints": "ep endpoints", "Rollout": "rollout rollouts",
    "NodePool": "nodepool nodepools", "ScaledObject": "scaledobject scaledobjects",
    "Certificate": "cert certificate certificates", "HTTPRoute": "httproute httproutes", "Gateway": "gateway gateways",
    "CustomResourceDefinition": "crd crds"}.items() for alias in aliases.split()}


_KUBE_PASCAL = re.compile(r"[A-Z][A-Za-z0-9]*[a-z][A-Za-z0-9]*")  # (a custom resource's kind: NodePool, EC2NodeClass)


def ref_kube(t):
    """A name written the way Kubernetes is talked about, or None:
      ("object", kind, name, namespace): deploy/relay-svc, Deployment/relay-svc, deployment.apps/x, NodePool/builder,
        or relay-svc.relay.svc.cluster.local, a Service's DNS name (the namespace None when it isn't given)
      ("name", name): a bare kebab-case name, beacon-runner-ingress: an object's, if the repository defines one
      ("context", name): an EKS cluster's ARN, which is how kubeconfigs name its context
      ("values", "image.tag"): .Values.image.tag, a Helm chart's value
      ("field", "spec.strategy.type"): spec.strategy.type, .metadata.labels: a key path in manifests"""
    t = ref_key(t)
    if m := re.fullmatch(r"(?:\{\{-?\s*)?\$?\.Values\.([A-Za-z_][\w-]*(?:\.[A-Za-z_][\w-]*)*)(?:\s*-?\}\})?", t):
        return "values", m.group(1)
    if m := re.fullmatch(r"\.?((?:spec|metadata)(?:\.[A-Za-z_][\w-]*(?:\[[^\]]*\])?)+)", t):
        return "field", re.sub(r"\[[^\]]*\]", "", m.group(1))
    if re.fullmatch(r"arn:aws:eks:[\w-]+:\d+:cluster/[\w.-]+", t):
        return "context", t
    if m := re.fullmatch(r"(?:https?://)?([a-z0-9](?:[a-z0-9-]*[a-z0-9])?)\.([a-z0-9-]+)\.svc(?:\.cluster\.local)?\.?"
                         r"(?::\d+)?(?:/\S*)?", t):
        return "object", "Service", m.group(1), m.group(2)
    if (m := re.fullmatch(rf"([A-Za-z][A-Za-z0-9]*)(?:\.[\w.]+)?/({_KUBE_NAME})", t)) and \
            not re.search(rf"\.(?:{_REF_EXT}|{_REF_MORE_EXT})$", m.group(2), re.I):
        kind = KUBE_KINDS.get(m.group(1).lower()) or (m.group(1) if _KUBE_PASCAL.fullmatch(m.group(1)) else None)
        if kind:
            return "object", kind, m.group(2), None
    if re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)+", t) and 4 <= len(t) <= 63 and re.search(r"[a-z]", t) and \
            not re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", t):  # (not a date, nor a UUID)
        return "name", t
    return None


def ref_kube_command(words):
    """What a kubectl, kubectx or helm command (its words) acts on, as ref_command says it, or None:
      ("kube", ("object", kind, name, namespace)): kubectl rollout restart deploy/x -n ns, kubectl logs <pod>
      ("kube", ("selector", key, value, namespace)): kubectl get pods -l app=x
      ("kube", ("context", name)): kubectl config use-context x, kubectx x, kubectl --context x get pods
      ("kube", ("values", key)): helm upgrade ... --set image.tag=$SHA
      ("kube", ("release", name, namespace)): helm status x, helm rollback x
      ("file", path): the chart helm installs, the values file it's given, the manifest kubectl applies"""
    prog = os.path.basename(words[0])
    if prog == "kubectx":
        return ("kube", ("context", words[1])) if len(words) == 2 and re.fullmatch(r"[\w:./@-]+", words[1]) else None
    opts, pos = _kube_args(words)
    placeholder = re.compile(r"[$<{*`]|://")
    get = lambda *flags: next((opts[f] for f in flags if opts.get(f) and not placeholder.search(opts[f])), None)  # noqa: E731
    ns = get("-n", "--namespace")
    ns = ns if ns and re.fullmatch(_KUBE_NAME, ns) else None
    if prog == "helm":
        if key := next((opts[f] for f in ("--set", "--set-string", "--set-file", "--set-json", "--set-literal")
                        if opts.get(f)), None):  # (the key: its value may well be $SHA)
            key = key.split(",")[0].split("=")[0]
            if re.fullmatch(r"[A-Za-z_][\w-]*(?:\.[A-Za-z_][\w-]*|\[\d+\])*", key):
                return "kube", ("values", re.sub(r"\[\d+\]", "", key))
        verb, args = (pos[0] if pos else ""), pos[1:]
        if verb == "diff" and args:  # (helm diff upgrade REL CHART)
            verb, args = args[0], args[1:]
        chart = args[1] if verb in ("upgrade", "install") and len(args) >= 2 else \
            args[-1] if verb in ("template", "lint", "package", "dependency", "dep", "show") and args else None
        if chart and not placeholder.search(chart) and ("/" in chart or chart.startswith(".")):
            return "file", chart.rstrip("/")
        if values := get("-f", "--values"):
            return "file", values
        if verb in ("uninstall", "delete", "status", "rollback", "history", "get") and args:
            rel = args[1] if verb == "get" and len(args) > 1 else args[0]
            return ("kube", ("release", rel, ns)) if re.fullmatch(_KUBE_NAME, rel) else None
        return None
    if pos[:1] == ["config"] and len(pos) > 2 and pos[1] in ("use-context", "set-context", "get-contexts",
                                                             "delete-context", "rename-context"):
        return "kube", ("context", pos[2])
    if (f := get("-f", "--filename", "-k", "--kustomize")) and f != "-":
        return "file", f
    verb, args = (pos[0] if pos else ""), pos[1:]
    if verb in ("rollout", "set", "top", "create") and args:  # (rollout restart deploy/x, set image deploy/x c=img)
        args = args[1:]
    kind = name = None
    if args and "/" in args[0]:
        k, _, name = args[0].partition("/")
        kind = KUBE_KINDS.get(k.split(".")[0].lower()) or (k if _KUBE_PASCAL.fullmatch(k) else None)
        name = name if kind else None
    elif args and (k := KUBE_KINDS.get(args[0].split(".")[0].lower())) and len(args) > 1:
        kind, name = k, args[1]
    elif args and verb in ("logs", "exec", "attach", "port-forward", "cp", "debug") and \
            not KUBE_KINDS.get(args[0].lower()):
        kind, name = "Pod", args[0].split(":")[0]  # (kubectl cp pod:/path .)
    if name and re.fullmatch(_KUBE_NAME, name):
        return "kube", ("object", kind, name, ns)
    if (sel := get("-l", "--selector")) and (m := re.fullmatch(r"([\w./-]+)=([\w.-]+)", sel.split(",")[0])):
        return "kube", ("selector", m.group(1), m.group(2), ns)
    if ns:
        return "kube", ("object", "Namespace", ns, None)
    if ctx := get("--context"):
        return "kube", ("context", ctx)
    return None


def ref_icon_slot(icon):
    """Where a link's logo goes, in markup: an invisible character LinkLabel makes the logo's width, naming the logo.
    It's U+2063, which Unicode breaks lines around as a letter: a line may end before the logo, never between the
    logo and its name. (A zero-width space with a word joiner after it still let the line end between them.)"""
    return f'<span font_family="baude-icon-{icon}">\u2063</span>' if icon else ""


def pr_pill_markup(inner, href, title, state):
    """inner (markup, no link in it) as a pull request's pill, linked to href: git's merge logo before it, on a rounded
    patch (LinkLabel draws them), in the colour of where it stands (PR_COLORS; state None: not known yet, a link's).
    Its places for the logo and the patch's ends are as wide whatever the state, so a state that comes later only
    colours it."""
    if color := PR_COLORS.get(state):
        inner = f'<span foreground="{color}">{inner}</span>'
    if state == "closed":
        inner = f"<s>{inner}</s>"
    return (f'<a href="{html.escape(href)}" title="{html.escape(title)}"><span underline="none">'
            f'<span font_family="baude-pill-{state or "unknown"}">\u2063</span>{inner}'
            f'<span font_family="baude-pill-end">\u2063</span></span></a>')


def pr_title(p, repo, number):
    """A pull request's pill's tooltip: its number, where it stands and its name."""
    said = PR_SAID.get((p or {}).get("state"))
    return " · ".join(x for x in (f"{repo}#{number}", said, (p or {}).get("title")) if x)


def pill_code_scale():
    """A pill's size of the text's: code's (code_scale), never more than the text's (a code font measured larger than
    the prose's, as one can be, leaves pills the prose's size)."""
    return min(1.0, code_scale())


# Well-known cloud, hosting and platform providers: named in replies all the time, and a link to "an outside product"
# (or to the one config file that mentions it) tells you nothing, so Signal never links them, nor their own products
# (a provider's name and a word after it: AWS Lambda, Cloudflare R2, Supabase Auth)
CLOUD_PROVIDERS = frozenset(n.casefold() for n in (
    "Cloudflare", "Vercel", "Netlify", "Supabase", "Firebase", "Shopify",
    "AWS", "Amazon Web Services", "GCP", "Google Cloud", "Google Cloud Platform", "Azure", "Microsoft Azure",
    "Heroku", "DigitalOcean", "Digital Ocean", "Fly.io", "Linode", "Akamai", "Fastly", "Hetzner", "OVH", "OVHcloud",
    "Render.com", "Railway.app", "PlanetScale", "Upstash", "Appwrite", "Auth0", "Okta", "Stripe", "Twilio", "SendGrid",
    "Mailgun", "Sentry", "Datadog", "New Relic", "GitHub Pages", "Oracle Cloud", "IBM Cloud", "Alibaba Cloud",
    "Backblaze"))


def ref_cloud_provider(surface):
    """Whether a name is one of CLOUD_PROVIDERS, or one of them and a word after it (a product of theirs): never a
    link. Written with a capital, in any case past that (AWS, Aws, Firebase); all in lower case (sentry, aws,
    stripe) it's as likely this code's own: a package, a module, a Deployment."""
    name = surface.strip()
    if name == name.lower():
        return False
    name = name.casefold()
    if name in CLOUD_PROVIDERS:
        return True
    head = name.rpartition(" ")[0]
    while head:  # (AWS Lambda, Google Cloud Storage: a provider's name, and words after it)
        if head in CLOUD_PROVIDERS:
            return True
        head = head.rpartition(" ")[0]
    return False


class LinkLabel(WrapLabel):
    """A label whose Signal links each have their logo before them (ref_icon): git's, Kubernetes', a database's, a
    file's… The markup keeps a place for it (ref_icon_slot), which this makes the logo's width (a Pango shape) and
    draws the logo in, at the text's size. Copied, the text has only invisible characters there. A chip (chip_markup:
    the Git page's branch names, in the line a commit's subject's in) and a pull request's pill (pr_pill_markup) keep
    a place at each end, their margins (a pill's logo too), and this draws their patches behind them, a piece on each
    line if one's broken across two; a chip's name it gives its colour."""

    GAP = 3  # px between a logo and its name
    CHIP_PAD, CHIP_SCALE, CHIP_RADIUS = 5, 0.78, 4  # px either side of a chip's name, its size of the text's, and its
    # corners' px: .git-ref's, as a chip on its own had them (a pill's corners too)
    PILL_IN, PILL_OUT = 4, 5  # px: a pill's margin before its logo, and after its name
    pill_scale = None  # (a pill's size of the text's: code's, as pr_pill draws its number; a log row's a chip's)

    def __init__(self, **kw):
        super().__init__(**kw)
        # ([(byte index, logo)], [(start, end, kind)], [(start, end, state)], the logos' size)
        self.icons, self.chips, self.pills, self.icon_px = [], [], [], 0

    def set_markup(self, markup):
        Gtk.Label.set_markup(self, markup)
        self.place_icons()

    def set_text(self, text):
        Gtk.Label.set_text(self, text)
        self.place_icons()

    def text_px(self):
        m = self.get_pango_context().get_metrics(None, None)
        return m.get_ascent(), m.get_descent(), max(10, round((m.get_ascent() + m.get_descent()) / Pango.SCALE * 0.78))

    def pill_ratio(self):
        """A pill's size of the text's: pill_scale, else its number's (pill_code_scale)."""
        return self.pill_scale or pill_code_scale()

    def pill_px(self):
        """A pill's logo's size, px."""
        return max(8, round(self.icon_px * self.pill_ratio()))

    def place_icons(self):
        """Find the places kept for logos, chips' and pills' ends, and make each its width (again, when the text's size
        changed); a chip's name in its colour (again, when the theme's changed)."""
        text = self.get_text()
        if "\u2063" not in text:
            if self.icons or self.chips or self.pills:
                self.icons, self.chips, self.pills = [], [], []
                self.set_attributes(None)
            return False
        names = re.findall(r'font_family="baude-(icon|chip|pill)-(\w+)">\u2063</span>', self.get_label())
        spots = [len(text[:m.start()].encode()) for m in re.finditer("\u2063", text)]
        ascent, descent, px = self.text_px()
        self.icon_px = px
        attrs, icons, chips, pills, opened, fg = Pango.AttrList(), [], [], [], None, self.get_color()
        for at, (kind, name) in zip(spots, names):
            if kind == "icon":
                width = px + self.GAP
                icons.append((at, name))
            elif name == "end":  # (a chip's or a pill's end)
                width = self.CHIP_PAD if kind == "chip" else self.PILL_OUT
                if opened is not None and opened[0] == "chip":  # (a chip's name, between the two, in its colour)
                    chips.append((opened[1], at + 3, opened[2]))
                    color = chip_colors(opened[2], fg)[0]
                    for a in (Pango.attr_foreground_new(*(round(v * 65535) for v in (color.red, color.green, color.blue))),
                              Pango.attr_foreground_alpha_new(max(1, round(color.alpha * 65535)))):
                        a.start_index, a.end_index = opened[1] + 3, at
                        attrs.insert(a)
                elif opened is not None:
                    pills.append((opened[1], at + 3, opened[2]))
                opened = None
            else:  # (a start: a chip's, name its kind; a pill's, its logo's place too, name where it stands)
                width = self.CHIP_PAD if kind == "chip" else self.PILL_IN + self.pill_px() + self.GAP
                opened = (kind, at, name)
            r = Pango.Rectangle()
            r.x, r.y, r.width, r.height = 0, -ascent, width * Pango.SCALE, ascent + descent
            a = Pango.attr_shape_new(r, r)
            a.start_index, a.end_index = at, at + 3  # (U+2063: 3 bytes)
            attrs.insert(a)
        self.icons, self.chips, self.pills = icons, chips, pills
        self.set_attributes(attrs)
        return False

    def do_css_changed(self, change):
        Gtk.Label.do_css_changed(self, change)
        # zoomed: the places take the text's new size as it's styled, before the frame's layout (resized once drawn,
        # the text came at its new size and the logos a frame later). Chips: their colours, light or dark, too
        if self.chips or (self.icons or self.pills) and self.text_px()[2] != self.icon_px:
            self.place_icons()

    def do_snapshot(self, snapshot):
        if self.chips or self.pills:
            self.draw_patches(snapshot)
        Gtk.Label.do_snapshot(self, snapshot)
        if not (self.icons or self.pills):
            return
        # in the text's colour, as vectors (a bitmap of it, shown at another scale than it was made at, or zoomed into,
        # went blocky), its corner on one of the screen's pixels so its straight lines stay sharp
        layout, (ox, oy) = self.get_layout(), self.get_layout_offsets()
        scale = screen_scale(self)
        ascent, descent, _px = self.text_px()
        fg = self.get_color()
        native = self.get_native()
        ok, origin = self.compute_point(native, Graphene.Point().init(0, 0)) if native is not None else (False, None)
        tx, ty = native.get_surface_transform() if native is not None else (0.0, 0.0)
        bx, by = (origin.x + tx, origin.y + ty) if ok else (0.0, 0.0)  # (where the label is on the surface)

        def pixel(v, base):
            return round((base + v) * scale) / scale - base
        small = (ascent - descent) / Pango.SCALE / 2 * (1 - self.pill_ratio())  # (a smaller pill's middle: lower)
        for at, icon, pad, color, px, lower in [(at, icon, 0, fg, self.icon_px, 0) for at, icon in self.icons] + \
                [(at, "merge", self.PILL_IN, pr_rgba(state, fg), self.pill_px(), small) for at, _end, state in self.pills]:
            size = max(8, round(px * scale))
            r = layout.index_to_pos(at)
            x = pixel(ox + r.x / Pango.SCALE + pad, bx)
            y = pixel(oy + (r.y + r.height / 2) / Pango.SCALE + lower - size / scale / 2, by)
            draw_ref_icon(snapshot, icon, x, y, size / scale, color)

    def draw_patches(self, snapshot):
        """The chips' and pills' patches, behind the text, their corners rounded as .git-ref's. A chip's as tall as
        its name's smaller text and a pixel more each way, in its kind's colour; a pill's the line's height less a
        little (a chip's, at a chip's size), in the colour of where its pull request stands, faint."""
        layout, (ox, oy) = self.get_layout(), self.get_layout_offsets()
        ascent, descent, _px = self.text_px()
        ascent, descent, fg = ascent / Pango.SCALE, descent / Pango.SCALE, self.get_color()

        def chip_box(scale):  # (top, height) of a patch, from the baseline
            return -(ascent * scale + 1), (ascent + descent) * scale + 2
        tall = round((ascent + descent) * 0.86)
        pill_box = chip_box(self.pill_ratio()) if self.pill_ratio() < 1 else (-(ascent - descent) / 2 - tall / 2, tall)
        patches = [(s, e, chip_colors(kind, fg)[1], chip_box(self.CHIP_SCALE)) for s, e, kind in self.chips]
        for s, e, state in self.pills:
            color = pr_rgba(state, fg)
            color.alpha = 0.09 if state in ("closed", "unknown") else 0.16
            patches.append((s, e, color, pill_box))
        for start, end, color, (top, h) in patches:
            for x, baseline, w in layout_runs(layout, start, end):
                rect = Graphene.Rect().init(ox + x, oy + baseline + top, w, h)
                rr = Gsk.RoundedRect()
                rr.init_from_rect(rect, self.CHIP_RADIUS)
                snapshot.push_rounded_clip(rr)
                snapshot.append_color(color, rect)
                snapshot.pop()


def pr_rgba(state, fg):
    """A pill's colour (PR_COLORS); for one whose state isn't known (yet, or at all: one older than those read), a
    link's, the accent's, as its number is (a link), so it's all one colour, logo, patch and number."""
    c = Gdk.RGBA()
    if not c.parse(PR_COLORS.get(state) or ""):
        c = accent_text_rgba()  # (@accent_color: a link's)
    return c


CHIP_TINTS = {"tag": ("#c88800", 0.16), "chat": ("#7f77dd", 0.18)}  # (.git-ref's: a tag's amber, this chat's violet)


def chip_colors(kind, fg):
    """A chip's name's colour and its patch's, as .git-ref has them: the accent's for a branch (local, head), the text's
    own faint for a remote's (fg: the text's colour), a tag's amber, this chat's violet. Light or dark, as it's now."""
    if kind in CHIP_TINTS:
        text = Gdk.RGBA()
        text.parse(CHIP_TINTS[kind][0])
        patch = text.copy()
        patch.alpha = CHIP_TINTS[kind][1]
    elif kind == "remote":
        text, patch = fg.copy(), fg.copy()
        text.alpha, patch.alpha = fg.alpha * 0.7, fg.alpha * 0.08
    else:
        text, patch = accent_text_rgba(), accent_rgba()  # (@accent_color, @accent_bg_color)
        patch.alpha = 0.14
    return text, patch


def chip_markup(text, kind):
    """text (plain) as a chip in a LinkLabel's markup: kind local, head (bold), remote, tag or chat, as .git-ref's are.
    Never broken across lines; a place kept at each end for its margins."""
    weight = "bold" if kind == "head" else "normal"
    return (f'<span allow_breaks="false"><span font_family="baude-chip-{kind}">\u2063</span>'
            f'<span size="{round(LinkLabel.CHIP_SCALE * 100)}%" weight="{weight}">{html.escape(text, quote=False)}'
            f'</span><span font_family="baude-chip-end">\u2063</span></span>')


def layout_runs(layout, start, end):
    """Where the text from byte start to end is on each line it's on: [(x, baseline, width)], px from the layout's
    corner."""
    out, it = [], layout.get_iter()
    while True:
        line = it.get_line_readonly()
        a, b = max(start, line.start_index), min(end, line.start_index + line.length)
        if a < b:
            _ink, logical = it.get_line_extents()
            x0, x1 = line.index_to_x(a, False), line.index_to_x(b, False)
            out.append(((logical.x + min(x0, x1)) / Pango.SCALE, it.get_baseline() / Pango.SCALE,
                        abs(x1 - x0) / Pango.SCALE))
        if not it.next_line():
            return out
