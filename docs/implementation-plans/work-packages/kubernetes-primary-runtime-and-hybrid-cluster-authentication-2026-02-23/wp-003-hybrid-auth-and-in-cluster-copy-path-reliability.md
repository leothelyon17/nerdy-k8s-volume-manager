# WP-003: Hybrid Auth and In-Cluster Copy Path Reliability

```yaml
WP_ID: WP-003
Domain: Service Layer
Priority: High
Estimated_Effort: 4-5 hours
Status: COMPLETE
Created_Date: 2026-02-23
```

## Description
Harden authentication and copy-path logic for both in-cluster and remote-cluster operation, with strong failure diagnostics.

## Deliverables
- [x] Harden auth/copy behavior in:
  - `src/nerdy_k8s_volume_manager/backup.py`
  - `src/nerdy_k8s_volume_manager/k8s.py`
- [x] Expand unit coverage in:
  - `tests/test_backup_manager.py`
  - `tests/test_k8s.py`
- [x] Verify deterministic behavior for:
  - In-cluster generated kubeconfig lifecycle.
  - Remote kubeconfig + context selection.
  - Actionable failures for missing token/CA/service host.

## Dependencies
- Blocked by: None
- Blocks: WP-004, WP-006, WP-007

## Acceptance Criteria
- [x] `kubectl cp` path works with generated in-cluster kubeconfig when no file-based kubeconfig exists.
- [x] Remote kubeconfig/context behavior remains unchanged and tested.
- [x] Error messages are stage-specific and operator-actionable.
- [x] Unit tests for hybrid auth paths pass with >=80% module coverage.
