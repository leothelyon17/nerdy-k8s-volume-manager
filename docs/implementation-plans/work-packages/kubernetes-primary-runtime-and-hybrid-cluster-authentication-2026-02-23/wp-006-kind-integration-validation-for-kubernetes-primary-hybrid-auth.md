# WP-006: KinD Integration Validation for Kubernetes-Primary + Hybrid Auth

```yaml
WP_ID: WP-006
Domain: Quality/Integration Testing
Priority: High
Estimated_Effort: 4-6 hours
Status: COMPLETE
Created_Date: 2026-02-23
Updated_Date: 2026-02-23
```

## Description
Extend integration testing to verify both the Kubernetes-primary in-cluster path and remote kubeconfig path in realistic cluster conditions.

## Deliverables
- [x] Extend integration harness and scenarios in:
  - `tests/integration/conftest.py`
  - `tests/integration/test_kind_backup_smoke.py`
  - `tests/integration/manifests/`
- [x] Add validation cases for:
  - In-cluster ServiceAccount runtime auth path.
  - Remote kubeconfig secret mounted path.
  - Backup copy behavior and failure diagnostics.
- [x] Document execution commands in `tests/integration/README.md`.

## Dependencies
- Blocked by: WP-001, WP-002, WP-003, WP-005
- Blocks: WP-007

## Acceptance Criteria
- [x] Integration suite verifies both primary and remote auth modes.
- [x] Failure output clearly indicates auth vs RBAC vs copy-stage errors.
- [x] Test artifacts are suitable for CI adoption.
- [x] Flake-safe setup/teardown behavior is documented.

## Execution Notes (2026-02-23)
- Extended KinD harness with remote kubeconfig secret-path materialization and in-cluster ServiceAccount token/CA synthesis for runtime auth-mode coverage.
- Added dedicated RBAC manifest (`tests/integration/manifests/smoke-auth-rbac.yaml`) containing both bound and unbound ServiceAccounts to validate allow/deny behavior.
- Expanded integration scenarios to cover:
  - remote kubeconfig auth success path
  - in-cluster ServiceAccount auth success path
  - explicit auth failure signal
  - explicit RBAC forbidden signal
  - explicit copy-stage failure signal
- Added RBAC impersonation checks to diagnostics output so CI failures surface immediate authn/authz hints.
- Updated integration README with mode-specific commands, CI invocation, and teardown/flake controls.
