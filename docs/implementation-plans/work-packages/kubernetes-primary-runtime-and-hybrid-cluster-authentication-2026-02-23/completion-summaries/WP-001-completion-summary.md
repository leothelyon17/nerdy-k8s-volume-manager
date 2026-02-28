# WP-001 Completion Summary

## Metadata
- Work Package: `WP-001`
- Title: Kubernetes App Manifest Hardening
- Date: `2026-02-23`
- Status: `PARTIAL` (pending cluster-side dry-run/live apply validation)

## Deliverables
- [x] Hardened app manifests:
  - `deploy/k8s/app/deployment.yaml`
  - `deploy/k8s/app/service.yaml`
  - `deploy/k8s/app/persistentvolumeclaim.yaml`
  - `deploy/k8s/app/kustomization.yaml`
- [x] Added explicit manifest validation and rollout checks to `deploy/k8s/README.md`.
- [x] Kept remote kubeconfig secret mount optional and documented optional behavior.
- [x] Added regression tests for manifest guardrails in `tests/test_k8s_manifests.py`.

## Acceptance Criteria
- [ ] App manifests apply cleanly in dry-run and live namespace tests.
  - Blocked in this environment because `kubectl` is not installed.
- [x] Deployment defaults to `NKVM_DEFAULT_AUTH_MODE=in-cluster`.
- [x] PVC/data mount paths align with runtime env variables and docs.
- [x] No privileged container settings introduced.

## Validation Run
- `./.venv/bin/pytest -q tests/test_k8s_manifests.py` -> `7 passed`
- `./.venv/bin/pytest -q` -> `70 passed, 2 skipped`
- `./.venv/bin/ruff check tests/test_k8s_manifests.py` -> `All checks passed`

## Follow-up Required
- Run the cluster-backed manifest checks from `deploy/k8s/README.md` in an environment with `kubectl` configured:
  - `kubectl apply --dry-run=server -k deploy/k8s/app`
  - RBAC dry-run applies
  - `kubectl -n nerdy-k8s-volume-manager rollout status deployment/nerdy-k8s-volume-manager --timeout=180s`
