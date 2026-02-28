# nerdy-k8s-volume-manager

Streamlit-based Kubernetes volume manager for:
- authenticating to clusters,
- discovering PVCs and workload ownership,
- backing up one or many PVCs as `.tar.gz` files,
- storing backup archives either on local runtime storage or a remote FTP/FTPS destination,
- recording backup outcomes and last successful timestamps.

## Primary Deployment Model

The primary deployment target is Kubernetes (in-cluster runtime using a ServiceAccount).
Default authentication mode in this path is `in-cluster`; operators can override to remote
clusters with kubeconfig path or pasted kubeconfig when needed.

Deployment and manifests:
- App manifests: `deploy/k8s/app/`
- RBAC manifests: `deploy/k8s/rbac/`
- Kubernetes deployment guide: `deploy/k8s/README.md`

## Architecture Decisions

- `docs/adrs/001-kubernetes-volume-backup-streamlit-mvp-architecture.md`
- `docs/adrs/002-kubernetes-primary-runtime-and-hybrid-cluster-authentication.md`

## Kubernetes Quick Start (Primary)

1. Build and push image:

```bash
docker build \
  --build-arg PYTHON_IMAGE=python:3.12-slim-bookworm \
  --build-arg KUBECTL_VERSION=v1.31.0 \
  -t ghcr.io/<org>/nerdy-k8s-volume-manager:<tag> .
docker push ghcr.io/<org>/nerdy-k8s-volume-manager:<tag>
```

2. Set image in `deploy/k8s/app/deployment.yaml`, then apply manifests:

```bash
kubectl apply -k deploy/k8s/app
kubectl apply -f deploy/k8s/rbac/serviceaccount.yaml
kubectl apply -f deploy/k8s/rbac/clusterrole-runtime.yaml
kubectl apply -f deploy/k8s/rbac/clusterrole-cluster-discovery.yaml
kubectl apply -f deploy/k8s/rbac/clusterrolebinding-cluster-discovery.yaml
```

3. Create one namespace-scoped runtime RoleBinding per allowlisted backup namespace using `deploy/k8s/rbac/rolebinding-runtime-template.yaml`.

4. Access UI:

```bash
kubectl -n nerdy-k8s-volume-manager port-forward svc/nerdy-k8s-volume-manager 8501:80
```

## Container Runtime Guardrail Verification

Verify non-root runtime identity, required `kubectl` binary, and deterministic env defaults:

```bash
docker run --rm --entrypoint /bin/sh ghcr.io/<org>/nerdy-k8s-volume-manager:<tag> -c 'id -u && id -g && whoami'
docker run --rm --entrypoint /bin/sh ghcr.io/<org>/nerdy-k8s-volume-manager:<tag> -c 'which kubectl && kubectl version --client --output=yaml | grep gitVersion'
docker run --rm --entrypoint /bin/sh ghcr.io/<org>/nerdy-k8s-volume-manager:<tag> -c 'printf "%s\n%s\n%s\n" "$NKVM_BACKUP_DIR" "$NKVM_METADATA_DB_PATH" "$NKVM_DEFAULT_AUTH_MODE"'
```

Expected output:
- UID/GID are non-root (default image runtime UID/GID: `10001`).
- `kubectl` exists and prints client version `v1.31.0` unless you intentionally override `KUBECTL_VERSION`.
- Env defaults include:
  - `/var/lib/nkvm/backups`
  - `/var/lib/nkvm/data/backups.db`
  - `in-cluster`

## Authentication Modes

Detailed auth guide with examples:
- `docs/operations/authentication-methods.md`

Supported UI modes:
- `In-cluster service account`: primary same-cluster operation.
- `Use kubeconfig path`: local or mounted kubeconfig for remote cluster APIs.
- `Paste kubeconfig`: short-lived/manual troubleshooting mode.

Remote cluster kubeconfig-in-secret example:
- `deploy/k8s/app/remote-kubeconfig-secret-example.yaml`

## Operator Quick Validation (One Backup)

After deployment and UI access, run one canary backup before broader operations:

1. Keep auth mode on `In-cluster service account` unless you are intentionally targeting a remote cluster.
2. Click `Refresh volume inventory`, pick one known PVC, then click `Backup selected volumes`.
3. Verify one archive exists in the configured backup directory:

```bash
ls -lh "${NKVM_BACKUP_DIR:-./backups}"/*.tar.gz
```

For full release gates, requirement traceability, and rollback pointers, use:
- `docs/operations/adr-002-release-acceptance-checklist.md`

## Standalone Runtime (Docker)

Run container locally and connect to clusters via mounted kubeconfig:

```bash
docker run --rm -p 8501:8501 \
  -v "$HOME/.kube:/home/nkvm/.kube:ro" \
  -v "$PWD/backups:/var/lib/nkvm/backups" \
  -v "$PWD/data:/var/lib/nkvm/data" \
  -e NKVM_DEFAULT_AUTH_MODE=kubeconfig \
  ghcr.io/<org>/nerdy-k8s-volume-manager:<tag>
```

Then use UI mode `Use kubeconfig path` with `/home/nkvm/.kube/config`.

## Local Python Runtime (Fallback)

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
PYTHONPATH=src streamlit run src/nerdy_k8s_volume_manager/app.py
```

## Configuration

Environment variables:

- `NKVM_BACKUP_DIR` (default: `./backups` for local, `/var/lib/nkvm/backups` in Docker image)
- `NKVM_METADATA_DB_PATH` (default: `./data/backups.db` for local, `/var/lib/nkvm/data/backups.db` in Docker image)
- `NKVM_HELPER_IMAGE` (default: `alpine:3.20`)
- `NKVM_HELPER_POD_TIMEOUT_SECONDS` (default: `120`)
- `NKVM_DISCOVERY_TIMEOUT_SECONDS` (default: `20`)
- `NKVM_MAX_NAMESPACE_SCAN` (default: `100`)
- `NKVM_DEFAULT_AUTH_MODE` (optional: `in-cluster`, `kubeconfig`, `paste`)

## Backup Destination Modes

- `Local pod/container volume` (default): writes archives to `NKVM_BACKUP_DIR`.
- `Remote destination`: uploads archives to `FTP`, `FTPS`, `SCP`, or `RSYNC` using UI-provided host, username, password, and remote directory.
  - Archives are staged locally during transfer and uploaded as `.tar.gz`.
  - Metadata is always written locally to `NKVM_METADATA_DB_PATH` on the app PVC/runtime volume.

### Helper Image Pinning Recommendation

```bash
export NKVM_HELPER_IMAGE="registry.example.com/nkvm-helper@sha256:<immutable-digest>"
```

## Security and Operations

- Security baseline: `docs/operations/security-baseline.md`
- Auth method guidance: `docs/operations/authentication-methods.md`
- ADR-002 release acceptance checklist: `docs/operations/adr-002-release-acceptance-checklist.md`
- Operations runbook: `docs/runbooks/mvp-operations.md`
- Restore procedure: `docs/runbooks/restore-procedure.md`

## Backup Flow

1. Connect to target cluster (service account, kubeconfig path, or pasted kubeconfig).
2. Discover PVCs and infer owner workloads from Pod owner references.
3. Select one or many PVCs.
4. For each PVC, create a short-lived helper pod with read-only PVC mount.
5. Create `.tar.gz` archive in helper pod and copy it locally in the app runtime.
6. If remote destination mode is selected, upload the archive to FTP/FTPS/SCP/RSYNC and store the remote artifact path.
7. Persist backup status and timestamps in SQLite.

## Tests

Unit and integration:

```bash
PYTHONPATH=src pytest -q
```

KinD integration suite:

```bash
NKVM_RUN_KIND_INTEGRATION=1 PYTHONPATH=src pytest -q -m integration tests/integration
```

## CI

Workflow: `.github/workflows/ci.yml`
