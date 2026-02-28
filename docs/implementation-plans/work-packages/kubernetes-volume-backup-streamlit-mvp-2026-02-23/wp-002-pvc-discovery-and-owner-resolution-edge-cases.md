# WP-002: PVC Discovery and Owner-Resolution Edge Case Coverage

```yaml
WP_ID: WP-002
Domain: Service Layer
Priority: High
Estimated_Effort: 5 hours
Status: COMPLETE
Created_Date: 2026-02-23
```

## Description
Harden PVC inventory and owner resolution for ReplicaSet/Deployment, Job/CronJob, and ambiguous/missing owner-reference scenarios.

## Deliverables
- [x] Improve owner-resolution behavior in `src/nerdy_k8s_volume_manager/k8s.py` for unresolved/multi-owner presentation.
- [x] Add unit tests for owner chain recursion and missing API objects in `tests/test_k8s.py`.
- [x] Add guardrails for large namespace scans (timeouts and error messaging strategy).

## Dependencies
- Blocked by: None
- Blocks: WP-004, WP-005, WP-006

## Acceptance Criteria
- [x] Owner mapping returns deterministic `Unknown`/`Multiple[...]` states when ambiguous.
- [x] Discovery succeeds/fails with actionable errors (no silent failure paths).
- [x] Unit tests cover controller resolution branches and `ApiException` handling.
