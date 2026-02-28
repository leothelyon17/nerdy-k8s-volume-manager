# Kubernetes Deployment (Primary)

`nerdy-k8s-volume-manager` is designed to run in Kubernetes first, using its own ServiceAccount for in-cluster discovery and backups.

## 1) Build and Push the Image

```bash
docker build \
  --build-arg PYTHON_IMAGE=python:3.12-slim-bookworm \
  --build-arg KUBECTL_VERSION=v1.31.0 \
  -t ghcr.io/<org>/nerdy-k8s-volume-manager:<tag> .
docker push ghcr.io/<org>/nerdy-k8s-volume-manager:<tag>
```

Update `deploy/k8s/app/deployment.yaml` to use your published image tag.

## 2) Verify Runtime Guardrails Before Deploy

```bash
docker run --rm --entrypoint /bin/sh ghcr.io/<org>/nerdy-k8s-volume-manager:<tag> -c 'id -u && id -g && whoami'
docker run --rm --entrypoint /bin/sh ghcr.io/<org>/nerdy-k8s-volume-manager:<tag> -c 'which kubectl && kubectl version --client --output=yaml | grep gitVersion'
docker run --rm --entrypoint /bin/sh ghcr.io/<org>/nerdy-k8s-volume-manager:<tag> -c 'printf "%s\n%s\n%s\n" "$NKVM_BACKUP_DIR" "$NKVM_METADATA_DB_PATH" "$NKVM_DEFAULT_AUTH_MODE"'
```

Expected results:
- Runtime identity is non-root (default UID/GID `10001`).
- `kubectl` is present and reports client version `v1.31.0` unless build arg override is used.
- Env defaults resolve to `/var/lib/nkvm/backups`, `/var/lib/nkvm/data/backups.db`, and `in-cluster`.

## 3) Apply Namespace, Storage, and App Resources

Validate manifests against the target cluster API first:

```bash
kubectl apply --dry-run=server -k deploy/k8s/app
kubectl apply --dry-run=server -f deploy/k8s/rbac/serviceaccount.yaml
kubectl apply --dry-run=server -f deploy/k8s/rbac/clusterrole-runtime.yaml
kubectl apply --dry-run=server -f deploy/k8s/rbac/clusterrole-cluster-discovery.yaml
kubectl apply --dry-run=server -f deploy/k8s/rbac/clusterrolebinding-cluster-discovery.yaml
```

Then apply the resources:

```bash
kubectl apply -k deploy/k8s/app
```

## 4) Apply RBAC

```bash
kubectl apply -f deploy/k8s/rbac/serviceaccount.yaml
kubectl apply -f deploy/k8s/rbac/clusterrole-runtime.yaml
kubectl apply -f deploy/k8s/rbac/clusterrole-cluster-discovery.yaml
kubectl apply -f deploy/k8s/rbac/clusterrolebinding-cluster-discovery.yaml
```

For each allowed workload namespace, create a `RoleBinding` from `deploy/k8s/rbac/rolebinding-runtime-template.yaml`.

## 5) Verify Rollout and Storage

```bash
kubectl -n nerdy-k8s-volume-manager rollout status deployment/nerdy-k8s-volume-manager --timeout=180s
kubectl -n nerdy-k8s-volume-manager get pods,svc,pvc
```

## 6) Optional Remote Cluster Access

Create a Secret with a remote-cluster kubeconfig and mount it in the pod:

```bash
kubectl -n nerdy-k8s-volume-manager create secret generic nkvm-remote-kubeconfig \
  --from-file=config=/path/to/remote-kubeconfig.yaml
```

The deployment mounts this secret at `/etc/nkvm/remote/config` only when the secret exists.
If the secret is absent, the pod still starts and the default in-cluster auth mode remains usable.
Use this path only when intentionally overriding the primary in-cluster runtime flow.

## 7) Access the UI

```bash
kubectl -n nerdy-k8s-volume-manager port-forward svc/nerdy-k8s-volume-manager 8501:80
```

Then open `http://localhost:8501`.

## Authentication Modes in the UI

- `In-cluster service account`: primary mode for same-cluster backups.
- `Use kubeconfig path`: use `/etc/nkvm/remote/config` for remote API endpoints.
- `Paste kubeconfig`: temporary operator-driven remote access.

Default/override guidance:
- Keep `In-cluster service account` as the normal operating mode for Kubernetes deployments.
- Switch to `Use kubeconfig path` only for remote-cluster operations (for example `/etc/nkvm/remote/config`).
- Revert to `In-cluster service account` after remote troubleshooting is complete.

## Release Acceptance and Rollback

Before production release, execute:
- `docs/operations/adr-002-release-acceptance-checklist.md`

For incident operations and rollback detail, use:
- `docs/runbooks/mvp-operations.md` (Operational Rollback section)
- `docs/operations/security-baseline.md` (RBAC validation and hardening checklist)
