# KinD Integration Test Harness

This suite creates a disposable KinD cluster, seeds a PVC + pod workload, and validates:

- remote kubeconfig Secret-mount auth path (`/etc/nkvm/remote/config` equivalent)
- in-cluster ServiceAccount runtime auth path
- PVC discovery and end-to-end backup via `BackupManager.backup_one()`
- artifact creation and metadata persistence in SQLite
- explicit failure diagnostics for auth vs RBAC vs copy-stage failures

## Prerequisites

- Docker daemon running
- `kind` installed
- `kubectl` installed

## Run Locally

Run the full KinD integration suite:

```bash
NKVM_RUN_KIND_INTEGRATION=1 PYTHONPATH=src pytest -q -m integration tests/integration
```

Run only remote kubeconfig-path coverage:

```bash
NKVM_RUN_KIND_INTEGRATION=1 PYTHONPATH=src pytest -q -m integration tests/integration/test_kind_backup_smoke.py -k remote_kubeconfig
```

Run only in-cluster ServiceAccount coverage:

```bash
NKVM_RUN_KIND_INTEGRATION=1 PYTHONPATH=src pytest -q -m integration tests/integration/test_kind_backup_smoke.py -k incluster_serviceaccount
```

Run failure-diagnostic validation cases:

```bash
NKVM_RUN_KIND_INTEGRATION=1 PYTHONPATH=src pytest -q -m integration tests/integration/test_kind_backup_smoke.py -k "auth_failure or rbac_failure or copy_stage_failure"
```

The `NKVM_RUN_KIND_INTEGRATION=1` guard prevents accidental KinD cluster creation during normal fast unit-test runs.

## CI-Friendly Invocation

For fail-fast behavior while keeping useful skip/error summaries:

```bash
NKVM_RUN_KIND_INTEGRATION=1 PYTHONPATH=src pytest -q -m integration tests/integration --maxfail=1 -ra
```

## Failure Diagnostics Signatures

- Auth failures: `KubernetesAuthenticationError` with `Kubernetes authentication setup failed ...`
- RBAC failures: `KubernetesDiscoveryError` with `API status 403 (Forbidden)`
- Copy-stage failures: backup result `message` contains `copy stage failed: ...`

When failures happen, diagnostics include:

- cluster nodes and pod inventory
- PVC/PV status
- namespace events
- source pod describe/log output
- helper pod list (`app.kubernetes.io/component=backup-helper`)
- RBAC impersonation checks (`kubectl auth can-i`) for bound and unbound ServiceAccounts

## Flake-Safe Setup and Teardown

- Session fixture uses unique cluster names (`nkvm-it-<uuid>`) to avoid collisions.
- A `finally` block always runs `kind delete cluster` even on test failures.
- Every test closes Kubernetes API clients (`api_client.close()`) after use.
- ServiceAccount tokens are short-lived and generated per-test from the disposable KinD cluster.
- Temporary kubeconfig/token/CA artifacts are stored under the fixture temp directory and discarded with the test harness workspace.
