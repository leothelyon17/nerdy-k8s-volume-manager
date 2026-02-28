# WP-002: RBAC Least-Privilege Validation and Binding Workflow

```yaml
WP_ID: WP-002
Domain: Infrastructure/Security
Priority: High
Estimated_Effort: 3-4 hours
Status: COMPLETE
Created_Date: 2026-02-23
Updated_Date: 2026-02-23
```

## Description
Review and tighten RBAC manifests and operator binding workflow so discovery and runtime permissions stay least-privilege and auditable.

## Deliverables
- [x] Review and tighten:
  - `deploy/k8s/rbac/clusterrole-runtime.yaml`
  - `deploy/k8s/rbac/clusterrole-cluster-discovery.yaml`
  - `deploy/k8s/rbac/clusterrolebinding-cluster-discovery.yaml`
  - `deploy/k8s/rbac/rolebinding-runtime-template.yaml`
- [x] Add explicit namespace-onboarding instructions in `deploy/k8s/rbac/README.md`.
- [x] Add RBAC validation checklist to `docs/operations/security-baseline.md`.

## Dependencies
- Blocked by: None
- Blocks: WP-006, WP-007

## Acceptance Criteria
- [x] Runtime permissions are namespace-scoped where possible.
- [x] Discovery permissions are read-only and intentionally separated.
- [x] Operators can follow docs to bind exactly required namespaces.
- [x] Security baseline contains a pass/fail preflight checklist.

## Execution Notes (2026-02-23)
- Confirmed runtime and discovery RBAC resources/verbs match concrete API usage in:
  - `src/nerdy_k8s_volume_manager/k8s.py`
  - `src/nerdy_k8s_volume_manager/backup.py`
- Tightened manifest auditability by adding explicit component labels and rule-level intent comments to all RBAC deliverable manifests.
- Added deterministic operator workflow for namespace onboarding/offboarding in `deploy/k8s/rbac/README.md`, including `kubectl auth can-i` preflight checks with ServiceAccount impersonation.
- Added release-gate RBAC pass/fail checklist to `docs/operations/security-baseline.md`.
- Added regression tests in `tests/test_rbac_manifests.py` to lock RBAC rule intent, binding semantics, and documentation checklist presence.
