# ADR-002 Release Acceptance Checklist

Use this checklist to approve releases for ADR-002 (`Kubernetes-primary runtime and hybrid cluster authentication`).
It provides requirement traceability, operational validation gates, and rollback pointers.

Related ADR:
- `docs/adrs/002-kubernetes-primary-runtime-and-hybrid-cluster-authentication.md`

## 1) Requirement Traceability

### Must-Have Requirements (Go/No-Go)

| Requirement | Implemented Artifacts | Verification Gate |
|---|---|---|
| REQ-1: Kubernetes deployment is the primary documented and operational path | `README.md`, `deploy/k8s/README.md`, `deploy/k8s/app/` | Kubernetes guide is the primary quick start and app manifests apply/roll out. |
| REQ-2: In-cluster ServiceAccount auth is first-class and reliable | `deploy/k8s/rbac/`, `src/nerdy_k8s_volume_manager/k8s.py`, `src/nerdy_k8s_volume_manager/backup.py`, `docs/operations/authentication-methods.md` | In-cluster mode connects and one canary backup succeeds end-to-end. |
| REQ-3: Remote cluster API endpoint support remains available | `deploy/k8s/app/remote-kubeconfig-secret-example.yaml`, `docs/operations/authentication-methods.md`, `src/nerdy_k8s_volume_manager/app.py` | Mounted kubeconfig path (`/etc/nkvm/remote/config`) override connects successfully. |
| REQ-4: Docker image runs both in Kubernetes and standalone | `Dockerfile`, `.dockerignore`, `README.md`, `deploy/k8s/README.md` | Image guardrail checks pass and standalone startup remains healthy. |

### Should-Have Requirements (Release Quality)

| Requirement | Implemented Artifacts | Verification Gate |
|---|---|---|
| REQ-5: Clear authentication examples for all methods | `docs/operations/authentication-methods.md`, `README.md`, `deploy/k8s/README.md` | Operator can follow examples without external clarification. |
| REQ-6: Minimal disruption to existing UI and backup workflows | `src/nerdy_k8s_volume_manager/app.py`, `docs/runbooks/mvp-operations.md` | Standard backup flow works unchanged in UI with clear auth-mode defaults. |

## 2) Preflight Validation Commands

### 2.1 Image and Runtime Guardrails

```bash
docker build \
  --build-arg PYTHON_IMAGE=python:3.12-slim-bookworm \
  --build-arg KUBECTL_VERSION=v1.31.0 \
  -t ghcr.io/<org>/nerdy-k8s-volume-manager:<tag> .
docker run --rm --entrypoint /bin/sh ghcr.io/<org>/nerdy-k8s-volume-manager:<tag> -c 'id -u && id -g && whoami'
docker run --rm --entrypoint /bin/sh ghcr.io/<org>/nerdy-k8s-volume-manager:<tag> -c 'which kubectl && kubectl version --client --output=yaml | grep gitVersion'
docker run --rm --entrypoint /bin/sh ghcr.io/<org>/nerdy-k8s-volume-manager:<tag> -c 'printf "%s\n%s\n%s\n" "$NKVM_BACKUP_DIR" "$NKVM_METADATA_DB_PATH" "$NKVM_DEFAULT_AUTH_MODE"'
```

Expected:
- Non-root UID/GID.
- `kubectl` present with expected client version.
- Defaults include `/var/lib/nkvm/backups`, `/var/lib/nkvm/data/backups.db`, `in-cluster`.

### 2.2 Kubernetes and RBAC Dry-Run + Apply

```bash
kubectl apply --dry-run=server -k deploy/k8s/app
kubectl apply --dry-run=server -f deploy/k8s/rbac/serviceaccount.yaml
kubectl apply --dry-run=server -f deploy/k8s/rbac/clusterrole-runtime.yaml
kubectl apply --dry-run=server -f deploy/k8s/rbac/clusterrole-cluster-discovery.yaml
kubectl apply --dry-run=server -f deploy/k8s/rbac/clusterrolebinding-cluster-discovery.yaml

kubectl apply -k deploy/k8s/app
kubectl apply -f deploy/k8s/rbac/serviceaccount.yaml
kubectl apply -f deploy/k8s/rbac/clusterrole-runtime.yaml
kubectl apply -f deploy/k8s/rbac/clusterrole-cluster-discovery.yaml
kubectl apply -f deploy/k8s/rbac/clusterrolebinding-cluster-discovery.yaml
```

Then follow RBAC pass/fail checks in:
- `docs/operations/security-baseline.md`

### 2.3 Integration Gate (WP-006 Dependency)

Run KinD integration smoke tests before final release sign-off:

```bash
NKVM_RUN_KIND_INTEGRATION=1 PYTHONPATH=src pytest -q -m integration tests/integration
```

## 3) Operator Smoke Validation (One Backup)

1. Access UI:

```bash
kubectl -n nerdy-k8s-volume-manager port-forward svc/nerdy-k8s-volume-manager 8501:80
```

2. Keep default mode `In-cluster service account`.
3. Refresh inventory, select one canary PVC, run backup.
4. Verify backup artifact exists:

```bash
ls -lh "${NKVM_BACKUP_DIR:-./backups}"/*.tar.gz
```

5. Verify metadata row exists:

```bash
sqlite3 "${NKVM_METADATA_DB_PATH:-./data/backups.db}" \
  "SELECT namespace,pvc_name,status,created_at FROM backup_history ORDER BY created_at DESC, id DESC LIMIT 5;"
```

6. Validate remote override path:
- Mount remote kubeconfig secret and select `Use kubeconfig path`.
- Set path `/etc/nkvm/remote/config`.
- Connect successfully, then revert to `In-cluster service account`.

## 4) Rollback Pointers

Use `docs/runbooks/mvp-operations.md` for full procedure. Fast containment:

1. Stop new backup runs in the UI.
2. Remove runtime write permissions in impacted namespaces:

```bash
kubectl -n <target-namespace> delete rolebinding nkvm-runtime
```

3. If needed, pause the app:

```bash
kubectl -n nerdy-k8s-volume-manager scale deployment nerdy-k8s-volume-manager --replicas=0
```

4. Roll deployment back to previous image revision:

```bash
kubectl -n nerdy-k8s-volume-manager rollout history deployment/nerdy-k8s-volume-manager
kubectl -n nerdy-k8s-volume-manager rollout undo deployment/nerdy-k8s-volume-manager
```

5. Resume operations only after RBAC/auth root cause is fixed and one canary backup passes.

## 5) Release Sign-Off Record

- Release candidate image tag: `<tag>`
- Cluster/context: `<context>`
- Operator: `<name>`
- Date: `<YYYY-MM-DD>`
- Result: `GO` / `NO-GO`
- Evidence links (logs, screenshots, command output): `<links-or-paths>`
