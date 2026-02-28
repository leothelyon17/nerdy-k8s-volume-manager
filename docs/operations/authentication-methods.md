# Authentication Methods

`nerdy-k8s-volume-manager` supports three authentication modes in the UI.

Default and override policy for Kubernetes deployments:
- Default: `In-cluster service account` (primary runtime mode).
- Remote override: `Use kubeconfig path` with mounted Secret path (for example `/etc/nkvm/remote/config`).
- Incident-only override: `Paste kubeconfig` for short-lived troubleshooting.
- After remote work, revert to `In-cluster service account`.

## 1) In-Cluster Service Account (Primary)

Use this when the app runs inside the same cluster it should back up.

How it works:
- `load_incluster_config()` authenticates Kubernetes API calls.
- Backup copy uses `kubectl cp` with a generated in-cluster kubeconfig.

Requirements:
- Pod runs with ServiceAccount `nkvm-runner`.
- RBAC from `deploy/k8s/rbac/` is applied.

UI selection:
- `Authentication` -> `In-cluster service account`.

## 2) Kubeconfig Path

Use this for remote cluster API endpoints or when running outside Kubernetes.

### Example A: Local developer machine

```bash
PYTHONPATH=src streamlit run src/nerdy_k8s_volume_manager/app.py
```

UI:
- `Authentication` -> `Use kubeconfig path`
- `Kubeconfig path` -> `~/.kube/config`
- Optional context -> `dev-cluster`

### Example B: In-cluster app targeting a remote cluster

Create Secret:

```bash
kubectl -n nerdy-k8s-volume-manager create secret generic nkvm-remote-kubeconfig \
  --from-file=config=/path/to/remote-kubeconfig.yaml
```

The Deployment mounts this file at `/etc/nkvm/remote/config`.
If the secret is not mounted, keep the default in-cluster mode.

UI:
- `Authentication` -> `Use kubeconfig path`
- `Kubeconfig path` -> `/etc/nkvm/remote/config`
- Optional context -> remote context name

## 3) Paste Kubeconfig

Use for short-lived troubleshooting sessions only.

UI:
- `Authentication` -> `Paste kubeconfig`
- Paste full kubeconfig content
- Optional context override

Security guidance:
- Treat pasted kubeconfig as sensitive.
- Use short-lived credentials where possible.
- Rotate credentials after incident-driven troubleshooting.

## UI Guardrails

Before `Connect`, the app validates auth input to reduce avoidable connection failures:
- `Use kubeconfig path`: path must exist, point to a readable UTF-8 file, and contain kubeconfig essentials (`apiVersion`, `clusters`, `contexts`, `users`).
- `Paste kubeconfig`: content must be valid YAML and include the same kubeconfig essentials before any Kubernetes client load is attempted.
- `In-cluster service account`: requires pod-level Kubernetes environment (`KUBERNETES_SERVICE_HOST`) and mounted service-account token.

## Standalone Docker Example

Run the app in Docker but authenticate to any cluster with a mounted kubeconfig:

```bash
docker run --rm -p 8501:8501 \
  -v "$HOME/.kube:/home/nkvm/.kube:ro" \
  -v "$PWD/backups:/var/lib/nkvm/backups" \
  -v "$PWD/data:/var/lib/nkvm/data" \
  -e NKVM_DEFAULT_AUTH_MODE=kubeconfig \
  ghcr.io/<org>/nerdy-k8s-volume-manager:<tag>
```

Then select `Use kubeconfig path` and set `/home/nkvm/.kube/config`.

## Release Gate Reference

For ADR-002 requirement traceability and go/no-go checks, use:
- `docs/operations/adr-002-release-acceptance-checklist.md`
