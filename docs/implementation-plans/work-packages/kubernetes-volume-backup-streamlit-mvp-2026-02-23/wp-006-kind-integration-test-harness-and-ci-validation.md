# WP-006: KinD Integration Test Harness and CI Validation Path

```yaml
WP_ID: WP-006
Domain: Quality/Infrastructure
Priority: High
Estimated_Effort: 6 hours
Status: COMPLETE
Created_Date: 2026-02-23
```

## Description
Add integration tests against a disposable KinD cluster to validate discovery and backup behavior end-to-end.

## Deliverables
- [x] Create KinD test scaffolding under `tests/integration/` with fixture setup/teardown.
- [x] Add integration tests for PVC discovery and backup execution path (smoke level).
- [x] Add CI execution instructions (or workflow file if repo CI is desired now).

## Dependencies
- Blocked by: WP-001, WP-003, WP-004, WP-005
- Blocks: WP-007

## Acceptance Criteria
- [x] Integration suite runs locally with documented prerequisites.
- [x] At least one full-path test verifies artifact creation + metadata persistence.
- [x] Failure diagnostics are actionable (logs surfaced in test output).
