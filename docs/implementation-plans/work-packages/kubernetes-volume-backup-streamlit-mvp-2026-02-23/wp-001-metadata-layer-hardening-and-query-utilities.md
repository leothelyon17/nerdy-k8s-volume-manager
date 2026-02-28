# WP-001: Metadata Layer Hardening and Query Utilities

```yaml
WP_ID: WP-001
Domain: Data Layer
Priority: High
Estimated_Effort: 4 hours
Status: DEFINED
Created_Date: 2026-02-23
```

## Description
Strengthen SQLite metadata behavior for reliable history and latest-success lookups under repeated backup operations.

## Deliverables
- [ ] Update `src/nerdy_k8s_volume_manager/metadata.py` with stricter query/ordering behavior and retention-ready helper methods.
- [ ] Add metadata edge-case tests in `tests/test_metadata.py` (same timestamp ordering, mixed statuses, empty history).
- [ ] Document metadata schema and query contract in `README.md`.

## Dependencies
- Blocked by: None
- Blocks: WP-004, WP-006, WP-008

## Acceptance Criteria
- [ ] `get_last_success_map()` and `get_recent_results()` are deterministic.
- [ ] New tests pass and metadata module coverage >=80%.
- [ ] No schema regression against existing `backup_history` table.
