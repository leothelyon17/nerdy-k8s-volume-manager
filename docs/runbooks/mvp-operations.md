# MVP Operations Runbook

This runbook covers day-2 operations for the Kubernetes Volume Backup Streamlit MVP:

- startup and configuration checks
- routine backup execution
- troubleshooting and incident recovery
- operational rollback actions

## Scope and Preconditions

- App version: `nerdy-k8s-volume-manager` MVP (`README.md` in this repo)
- Required tools:
  - `kubectl`
  - `docker` (for image build/publish)
- Required access:
  - Kubernetes credentials (kubeconfig path, pasted kubeconfig, or in-cluster ServiceAccount)
  - RBAC from `deploy/k8s/rbac/`
- Required persisted paths:
  - in-cluster default: `/var/lib/nkvm/backups`, `/var/lib/nkvm/data/backups.db`
  - standalone default: `./backups`, `./data/backups.db`

## ADR-002 Release Gate (Before Production Release)

Use this checklist as the mandatory release gate:
- `docs/operations/adr-002-release-acceptance-checklist.md`

The checklist contains:
- ADR-002 requirement traceability to implementation artifacts.
- Go/no-go validation commands for in-cluster default and remote override auth paths.
- Rollback pointers and evidence capture fields.

## Startup Procedure

### Primary: Run in Kubernetes

```bash
docker build -t ghcr.io/<org>/nerdy-k8s-volume-manager:<tag> .
docker push ghcr.io/<org>/nerdy-k8s-volume-manager:<tag>
```

```bash
kubectl apply -k deploy/k8s/app
kubectl apply -f deploy/k8s/rbac/serviceaccount.yaml
kubectl apply -f deploy/k8s/rbac/clusterrole-runtime.yaml
kubectl apply -f deploy/k8s/rbac/clusterrole-cluster-discovery.yaml
kubectl apply -f deploy/k8s/rbac/clusterrolebinding-cluster-discovery.yaml
```

Create one `RoleBinding` from `deploy/k8s/rbac/rolebinding-runtime-template.yaml` for each approved target namespace.

Access UI:

```bash
kubectl -n nerdy-k8s-volume-manager port-forward svc/nerdy-k8s-volume-manager 8501:80
```

### Standalone Fallback: Local Python Runtime

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

```bash
install -d -m 700 backups data
touch data/backups.db
chmod 600 data/backups.db
```

```bash
PYTHONPATH=src streamlit run src/nerdy_k8s_volume_manager/app.py
```

### Optional Runtime Overrides

```bash
export NKVM_BACKUP_DIR="./backups"
export NKVM_METADATA_DB_PATH="./data/backups.db"
export NKVM_HELPER_IMAGE="alpine:3.20"
export NKVM_HELPER_POD_TIMEOUT_SECONDS="120"
export NKVM_DISCOVERY_TIMEOUT_SECONDS="20"
export NKVM_MAX_NAMESPACE_SCAN="100"
```

In the UI sidebar, choose auth mode and connect. For in-cluster deployments, default auth mode is
`In-cluster service account`.

## Standard Backup Execution

1. Connect to the target cluster from the sidebar.
2. Set an optional namespace filter and click `Refresh volume inventory`.
3. Select one or more volumes in `Backup Selection`.
4. Choose batch settings (`Sequential (available)` is current runtime behavior).
5. Click `Backup selected volumes`.
6. Review:
  - `Latest Backup Run` for per-volume outcome
  - `Actionable Failures` for stage-specific next steps
  - `Recent Backup History` for persisted history

## Post-Backup Validation

1. Confirm archive files exist in configured backup path:

```bash
ls -lh "${NKVM_BACKUP_DIR:-./backups}"/*.tar.gz
```

2. Inspect archive contents for one artifact:

```bash
tar -tzf "<archive-path>.tar.gz" | head -n 20
```

3. Validate checksum (if checksum was recorded in UI history):

```bash
sha256sum "<archive-path>.tar.gz"
```

4. Confirm metadata persistence:

```bash
sqlite3 "${NKVM_METADATA_DB_PATH:-./data/backups.db}" \
  "SELECT namespace,pvc_name,status,created_at,backup_path FROM backup_history ORDER BY created_at DESC, id DESC LIMIT 20;"
```

## Incident Scenarios and Recovery Actions

| Scenario | Typical Signal | Recovery Actions |
|---|---|---|
| Cluster connection failure | UI shows `Connection failed: ...` | Validate kubeconfig path/context, verify cluster reachability (`kubectl get ns`), retry with correct auth mode. |
| Discovery failure | `Kubernetes discovery failed while trying to ...` | Check namespace filter, `NKVM_MAX_NAMESPACE_SCAN`, and RBAC for `namespaces`, `persistentvolumeclaims`, `pods`, `replicasets`, `jobs`. |
| `create stage failed` | Backup result message includes `create stage failed` | Confirm RoleBinding exists for target namespace and helper image pull is allowed. |
| `wait stage failed` | Backup result message includes `wait stage failed` | Inspect helper pod events, raise `NKVM_HELPER_POD_TIMEOUT_SECONDS`, verify node scheduling/image pull health. |
| `exec stage failed` | Backup result message includes `exec stage failed` | Validate helper image has `sh` and `tar`, verify PVC is mounted/readable. |
| `copy stage failed` | Backup result message includes `copy stage failed` | Ensure `kubectl` is installed in `PATH`, and active kubeconfig/context points to the same cluster used by the app. |
| `checksum stage failed` | Backup result message includes `checksum stage failed` | Validate local backup directory write access and disk free space; re-run backup after fixing storage issue. |
| `cleanup stage failed` | Backup result message includes `cleanup stage failed` | Manually remove helper pod and verify delete permissions: `kubectl -n <ns> delete pod <helper-pod> --ignore-not-found`. |

## Rapid Triage Commands

```bash
kubectl get pods -A -l app.kubernetes.io/component=backup-helper
kubectl get events -A --sort-by=.lastTimestamp | tail -n 40
kubectl auth can-i create pods --namespace <target-namespace>
kubectl auth can-i create pods/exec --namespace <target-namespace>
```

## Operational Rollback

Use this when backup execution causes repeated failures or operational risk.
Also see rollback pointers in `docs/operations/adr-002-release-acceptance-checklist.md`.

1. Freeze backup operations:
  - Tell operators to stop new backup runs in the UI.
  - Keep discovery-only usage if needed.
2. Revoke write-path permission quickly (namespace by namespace):

```bash
kubectl -n <target-namespace> delete rolebinding nkvm-runtime
```

3. Keep read-only discovery permission if needed (`nkvm-cluster-discovery` binding can remain).
4. Preserve evidence:
  - export latest UI failure details
  - archive `data/backups.db`
  - collect Kubernetes events/helper pod logs
5. Restore normal operations only after:
  - RBAC and root cause are corrected
  - at least one canary PVC backup succeeds end-to-end

## Escalation

Escalate to platform/Kubernetes owners when:

- API server errors or cluster-wide scheduling/image-pull failures persist
- RBAC changes are required outside approved namespace allowlist flow
- repeated checksum/copy failures indicate host storage instability
