#!/usr/bin/env python3
"""A made-up repository that runs on Kubernetes, a kubeconfig, and a Claude Code chat about them: Signal's
Kubernetes names, for the tests and for measuring.

    tests/kube_fixture.py OUT

writes OUT/repo (a git repository: a Helm chart installed by a workflow, a kustomize base and overlay, a CronJob),
OUT/kubeconfig (two contexts, with made-up credentials that must never show up anywhere), OUT/session.jsonl (the
chat, as Claude Code saves one: your messages, Claude's kubectl and Read calls, its replies), OUT/messages.json
(the replies' text) and OUT/reference.json (every name a reader may want to open, with its place), then prints the
commands that replay the chat and score it:

    KUBECONFIG=OUT/kubeconfig tests/ref_replay.py OUT/session.jsonl OUT/repo OUT/result.json [--sonnet 1]
    tests/signal_score.py OUT/result.json OUT/messages.json OUT/reference.json
"""
import json
import os
import subprocess
import sys

SECRETS = ("kube-test-token-DO-NOT-LEAK", "a3ViZS10ZXN0LWtleS1ETy1OT1QtTEVBSw", "TEST-CERTIFICATE-DO-NOT-LEAK")

FILES = {
    "charts/app/Chart.yaml": "apiVersion: v2\nname: app\ndescription: The preview runner\nversion: 0.3.0\n",
    "charts/app/values.yaml": "nameOverride: \"\"\nimage:\n  repository: ghcr.io/example/runner\n  tag: latest\n"
                              "runner:\n  previewPort: 8790\n  replicas: 2\ningress:\n  enabled: true\n"
                              "  host: preview.example.test\n",
    "charts/app/values-production.yaml": "image:\n  tag: \"1.4.2\"\nrunner:\n  replicas: 4\n",
    "charts/app/templates/_helpers.tpl":
        "{{- define \"app.name\" -}}\n{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix \"-\" }}\n"
        "{{- end }}\n\n{{- define \"app.fullname\" -}}\n{{- $name := default .Chart.Name .Values.nameOverride }}\n"
        "{{- if contains $name .Release.Name }}\n{{- .Release.Name | trunc 63 | trimSuffix \"-\" }}\n{{- else }}\n"
        "{{- printf \"%s-%s\" .Release.Name $name | trunc 63 | trimSuffix \"-\" }}\n{{- end }}\n{{- end }}\n",
    "charts/app/templates/runner-deployment.yaml":
        "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: {{ .Release.Name }}-runner\n  labels:\n"
        "    app: {{ .Release.Name }}-runner\nspec:\n  replicas: {{ .Values.runner.replicas }}\n  strategy:\n"
        "    type: Recreate\n  selector:\n    matchLabels:\n      app: {{ .Release.Name }}-runner\n  template:\n"
        "    metadata:\n      labels:\n        app: {{ .Release.Name }}-runner\n    spec:\n      containers:\n"
        "        - name: runner\n          image: \"{{ .Values.image.repository }}:{{ .Values.image.tag }}\"\n"
        "          ports:\n            - containerPort: {{ .Values.runner.previewPort }}\n",
    "charts/app/templates/runner-service.yaml":
        "apiVersion: v1\nkind: Service\nmetadata:\n  name: {{ .Release.Name }}-runner\nspec:\n  selector:\n"
        "    app: {{ .Release.Name }}-runner\n  ports:\n    - port: 80\n      targetPort: {{ .Values.runner.previewPort }}\n",
    "charts/app/templates/ingress.yaml":
        "{{- if .Values.ingress.enabled }}\napiVersion: networking.k8s.io/v1\nkind: Ingress\nmetadata:\n"
        "  name: {{ include \"app.fullname\" . }}-runner-ingress\nspec:\n  rules:\n    - host: {{ .Values.ingress.host }}\n"
        "      http:\n        paths:\n          - path: /\n            pathType: Prefix\n            backend:\n"
        "              service:\n                name: {{ .Release.Name }}-runner\n                port:\n"
        "                  number: 80\n{{- end }}\n",
    ".github/workflows/deploy.yml":
        "name: deploy\non: push\njobs:\n  deploy:\n    runs-on: ubuntu-latest\n    steps:\n"
        "      - uses: actions/checkout@v4\n      - name: Deploy the runner\n        run: |\n"
        "          helm upgrade --install beacon ./charts/app \\\n            -n beacon -f charts/app/values-production.yaml \\\n"
        "            --set image.tag=${{ github.sha }}\n",
    "deploy/k8s/base/relay.yaml":
        "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: relay-svc\n  namespace: relay\n  labels:\n"
        "    app: relay-svc\nspec:\n  selector:\n    matchLabels:\n      app: relay-svc\n  template:\n    metadata:\n"
        "      labels:\n        app: relay-svc\n    spec:\n      containers:\n        - name: relay\n"
        "          image: ghcr.io/example/relay:2.1\n          envFrom:\n            - configMapRef:\n"
        "                name: relay-config\n---\napiVersion: v1\nkind: Service\nmetadata:\n  name: relay-svc\n"
        "  namespace: relay\nspec:\n  selector:\n    app: relay-svc\n  ports:\n    - port: 8000\n",
    "deploy/k8s/base/kustomization.yaml":
        "resources:\n  - relay.yaml\nconfigMapGenerator:\n  - name: relay-config\n    literals:\n      - LOG_LEVEL=info\n",
    "deploy/k8s/overlays/prod/kustomization.yaml": "resources:\n  - ../../base\nnamePrefix: prod-\nnamespace: relay-prod\n",
    "deploy/k8s/cleanup-cronjob.yaml":
        "apiVersion: batch/v1\nkind: CronJob\nmetadata:\n  name: nightly-cleanup\n  namespace: relay\nspec:\n"
        "  schedule: \"0 3 * * *\"\n  jobTemplate:\n    spec:\n      template:\n        spec:\n          containers:\n"
        "            - name: cleanup\n              image: ghcr.io/example/relay:2.1\n          restartPolicy: OnFailure\n",
    "services/relay-svc/app.py": "def health():\n    return {\"ok\": True}\n",
    "README.md": "# silo\n\nThe preview runner (charts/app) and the relay service (deploy/k8s).\n",
}

KUBECONFIG = f"""apiVersion: v1
kind: Config
clusters:
- cluster:
    certificate-authority-data: {SECRETS[2]}
    server: https://k3d-dev.local:6443
  name: k3d-dev
- cluster:
    certificate-authority-data: {SECRETS[2]}
    server: https://ABCDEF.gr7.eu-west-1.eks.amazonaws.com
  name: arn:aws:eks:eu-west-1:111122223333:cluster/demo-prod
contexts:
- context:
    cluster: k3d-dev
    namespace: relay
    user: admin@k3d-dev
  name: k3d-dev
- context:
    cluster: arn:aws:eks:eu-west-1:111122223333:cluster/demo-prod
    namespace: beacon
    user: arn:aws:eks:eu-west-1:111122223333:cluster/demo-prod
  name: prod
current-context: k3d-dev
preferences: {{}}
users:
- name: admin@k3d-dev
  user:
    client-certificate-data: {SECRETS[2]}
    client-key-data: {SECRETS[1]}
- name: arn:aws:eks:eu-west-1:111122223333:cluster/demo-prod
  user:
    token: {SECRETS[0]}
    exec:
      apiVersion: client.authentication.k8s.io/v1beta1
      command: aws
      env:
      - name: AWS_PROFILE
        value: demo-prod
"""

REPLIES = [
    "The `beacon-runner` Deployment comes from `charts/app/templates/runner-deployment.yaml`, which the deploy workflow "
    "installs with `helm upgrade --install beacon ./charts/app`. Its pod `beacon-runner-6c9f7d8b4-q2w7z` is in "
    "CrashLoopBackOff: the container listens on `.Values.runner.previewPort` (8790), and `spec.strategy.type` is "
    "`Recreate`, so each rollout drops the old pod before the new one is ready. The ingress, "
    "`beacon-app-runner-ingress`, still routes to 8790. I checked this in the `prod` context (namespace `beacon`).",

    "Relay is plain kustomize: `deploy/relay-svc` and `svc/relay-svc` are in `deploy/k8s/base/relay.yaml`, and the "
    "prod overlay renames them `prod-relay-svc`, in namespace `relay-prod`. Its settings come from the generated "
    "`relay-config-7h2m9c4t8k` ConfigMap. Other services reach it at `relay-svc.relay.svc.cluster.local:8000`. To "
    "restart it, `kubectl -n relay rollout restart deploy/relay-svc`; to watch it, "
    "`kubectl get pods -l app=relay-svc -n relay`. The `nightly-cleanup` CronJob's last pod, "
    "`nightly-cleanup-29351520-x7kq2`, finished fine. Locally I used the `k3d-dev` context. A `queue-worker` "
    "Deployment would help here, but there isn't one yet.",

    "Set `image.tag` in `values-production.yaml` (it's `1.4.2` now), or pass it on the command line: "
    "`helm upgrade --install beacon ./charts/app --set image.tag=$SHA`, as the workflow does. `helm status beacon` "
    "shows the release afterwards. The runner ingress picks up the new pods by itself. If it goes wrong, run "
    "`helm rollback beacon` and look at `kubectl logs deploy/beacon-runner`.",
]

STEPS = [
    ("you", "The preview runner keeps restarting in prod. Can you check why?"),
    ("bash", "kubectl --context prod -n beacon get pods",
     "NAME                            READY   STATUS             RESTARTS   AGE\n"
     "beacon-runner-6c9f7d8b4-q2w7z   0/1     CrashLoopBackOff   7          14m"),
    ("read", "charts/app/templates/runner-deployment.yaml", {4: "  name: {{ .Release.Name }}-runner",
                                                              10: "    type: Recreate"}),
    ("reply", REPLIES[0]),
    ("you", "And the relay service?"),
    ("bash", "kubectl --context k3d-dev -n relay get deploy,svc,cm",
     "NAME                        READY   UP-TO-DATE   AVAILABLE   AGE\ndeployment.apps/relay-svc   1/1     1  1  3d\n"
     "NAME                TYPE        CLUSTER-IP     PORT(S)    AGE\nservice/relay-svc   ClusterIP   10.43.12.7     8000/TCP   3d\n"
     "NAME                            DATA   AGE\nconfigmap/relay-config-7h2m9c4t8k   1      3d"),
    ("reply", REPLIES[1]),
    ("you", "Bump the runner image for prod."),
    ("reply", REPLIES[2]),
]

# every name a reader may want to open in each reply (its index in REPLIES), with its class and place: what
# tests/signal_score.py scores against. A kubeconfig context's place is "kubeconfig:<name>".
REFERENCE = [
    (0, "beacon-runner", "k8s", "yes", "charts/app/templates/runner-deployment.yaml"),
    (0, "charts/app/templates/runner-deployment.yaml", "file", "yes", "charts/app/templates/runner-deployment.yaml"),
    (0, "deploy workflow", "file", "yes", ".github/workflows/deploy.yml"),
    (0, "helm upgrade --install beacon ./charts/app", "cli", "yes", "charts/app"),
    (0, "beacon-runner-6c9f7d8b4-q2w7z", "k8s", "yes", "charts/app/templates/runner-deployment.yaml"),
    (0, ".Values.runner.previewPort", "config", "yes", "charts/app/values.yaml"),
    (0, "spec.strategy.type", "config", "yes", "charts/app/templates/runner-deployment.yaml"),
    (0, "beacon-app-runner-ingress", "k8s", "yes", "charts/app/templates/ingress.yaml"),
    (0, "prod", "k8s", "yes", "kubeconfig:prod"),
    (0, "beacon", "k8s", "yes", ".github/workflows/deploy.yml"),
    (1, "Relay", "service", "yes", "deploy/k8s/base"),
    (1, "deploy/relay-svc", "k8s", "yes", "deploy/k8s/base/relay.yaml"),
    (1, "svc/relay-svc", "k8s", "yes", "deploy/k8s/base/relay.yaml"),
    (1, "deploy/k8s/base/relay.yaml", "file", "yes", "deploy/k8s/base/relay.yaml"),
    (1, "prod overlay", "file", "yes", "deploy/k8s/overlays/prod"),
    (1, "prod-relay-svc", "k8s", "yes", "deploy/k8s/base/relay.yaml"),
    (1, "relay-prod", "k8s", "yes", "deploy/k8s/overlays/prod/kustomization.yaml"),
    (1, "relay-config-7h2m9c4t8k", "k8s", "yes", "deploy/k8s/base/kustomization.yaml"),
    (1, "relay-svc.relay.svc.cluster.local:8000", "k8s", "yes", "deploy/k8s/base/relay.yaml"),
    (1, "kubectl -n relay rollout restart deploy/relay-svc", "cli", "yes", "deploy/k8s/base/relay.yaml"),
    (1, "kubectl get pods -l app=relay-svc -n relay", "cli", "yes", "deploy/k8s/base/relay.yaml"),
    (1, "nightly-cleanup", "k8s", "yes", "deploy/k8s/cleanup-cronjob.yaml"),
    (1, "nightly-cleanup-29351520-x7kq2", "k8s", "yes", "deploy/k8s/cleanup-cronjob.yaml"),
    (1, "k3d-dev", "k8s", "yes", "kubeconfig:k3d-dev"),
    (1, "queue-worker", "k8s", "proposed", None),
    (2, "image.tag", "config", "yes", "charts/app/values-production.yaml"),
    (2, "values-production.yaml", "file", "yes", "charts/app/values-production.yaml"),
    (2, "helm upgrade --install beacon ./charts/app --set image.tag=$SHA", "cli", "yes", "charts/app"),
    (2, "workflow", "file", "yes", ".github/workflows/deploy.yml"),
    (2, "helm status beacon", "cli", "yes", ".github/workflows/deploy.yml"),
    (2, "runner ingress", "k8s", "yes", "charts/app/templates/ingress.yaml"),
    (2, "helm rollback beacon", "cli", "yes", ".github/workflows/deploy.yml"),
    (2, "kubectl logs deploy/beacon-runner", "cli", "yes", "charts/app/templates/runner-deployment.yaml"),
]


def make_repo(folder):
    """The repository, committed (a fixed date, so its commits are the same every time)."""
    for rel, text in FILES.items():
        os.makedirs(os.path.dirname(os.path.join(folder, rel)) or folder, exist_ok=True)
        with open(os.path.join(folder, rel), "w") as f:
            f.write(text)
    env = {**os.environ, "GIT_AUTHOR_DATE": "2026-10-01T09:00:00Z", "GIT_COMMITTER_DATE": "2026-10-01T09:00:00Z"}
    for args in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "The runner chart and relay"]):
        subprocess.run(["git", "-c", "user.name=T", "-c", "user.email=t@t", *args], cwd=folder, check=True,
                       capture_output=True, env=env)
    return folder


def make_kubeconfig(path):
    with open(path, "w") as f:
        f.write(KUBECONFIG)
    os.chmod(path, 0o600)
    return path


def records(folder, sid, steps=STEPS):
    """The chat as Claude Code writes it: one record per message, each with the folder it ran in. steps: ("you",
    text), ("reply", text), ("read", path, {line: text}), ("grep", pattern, ["path:line:text"]), ("bash", command,
    output[, its description]), ("edit", path, new text)."""
    out, state = [], {"parent": None, "n": 0}

    def add(kind, content):
        state["n"] += 1
        uid = f"{sid[:8]}-{state['n']}"
        out.append({"type": kind, "uuid": uid, "parentUuid": state["parent"], "cwd": folder, "sessionId": sid,
                    "isSidechain": False, "timestamp": f"2026-10-20T10:{state['n']:02d}:00Z",
                    "message": {"role": kind, "content": content}})
        state["parent"] = uid
    for step in steps:
        if step[0] == "you":
            add("user", step[1])
        elif step[0] == "reply":
            add("assistant", [{"type": "text", "text": step[1]}])
        else:
            tid = f"toolu_{step[0]}{state['n']}"
            name, inp = {"read": ("Read", {"file_path": os.path.join(folder, step[1])}),
                         "edit": ("Edit", {"file_path": os.path.join(folder, step[1]), "old_string": "",
                                           "new_string": str(step[2])}),
                         "grep": ("Grep", {"pattern": step[1], "output_mode": "content", "-n": True}),
                         "bash": ("Bash", {"command": step[1], "description": step[3] if len(step) > 3 else
                                           "Look at the cluster"})}[step[0]]
            add("assistant", [{"type": "tool_use", "id": tid, "name": name, "input": inp}])
            body = "\n".join(f"{k:6}→{v}" for k, v in step[2].items()) if step[0] == "read" else \
                "\n".join(step[2]) if step[0] == "grep" else "The file has been updated." if step[0] == "edit" else step[2]
            add("user", [{"type": "tool_result", "tool_use_id": tid, "content": body}])
    return out


def write_session(path, folder, sid, steps=STEPS):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("\n".join(json.dumps(r) for r in records(folder, sid, steps)) + "\n")
    return path


def reference():
    return [{"message": m, "surface": s, "class": c, "exists": e, "path": p} for m, s, c, e, p in REFERENCE]


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    out = os.path.abspath(sys.argv[1])
    os.makedirs(out, exist_ok=True)
    repo = make_repo(os.path.join(out, "repo"))
    make_kubeconfig(os.path.join(out, "kubeconfig"))
    write_session(os.path.join(out, "session.jsonl"), repo, "4b0e0000-0000-4000-8000-00000000c0de")
    with open(os.path.join(out, "messages.json"), "w") as f:
        json.dump(REPLIES, f, indent=1)
    with open(os.path.join(out, "reference.json"), "w") as f:
        json.dump(reference(), f, indent=1)
    here = os.path.dirname(os.path.abspath(__file__))
    print(f"KUBECONFIG={out}/kubeconfig python3 {here}/ref_replay.py {out}/session.jsonl {out}/repo {out}/result.json")
    print(f"python3 {here}/signal_score.py {out}/result.json {out}/messages.json {out}/reference.json")


if __name__ == "__main__":
    main()
