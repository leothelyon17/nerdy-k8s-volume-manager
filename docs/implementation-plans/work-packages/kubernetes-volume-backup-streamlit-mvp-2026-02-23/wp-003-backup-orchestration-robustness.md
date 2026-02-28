# WP-003: Backup Orchestration Robustness

```yaml
WP_ID: WP-003
Domain: Service Layer
Priority: High
Estimated_Effort: 5 hours
Status: COMPLETE
Created_Date: 2026-02-23
```

## Description
Improve backup execution reliability around helper pod lifecycle, tar command execution, timeout handling, and archive validation.

## Deliverables
- [x] Refine backup flow in `src/nerdy_k8s_volume_manager/backup.py` (clear failure reasons for create/wait/exec/copy/checksum stages).
- [x] Add bounded retry logic for transient pod startup failures.
- [x] Add unit tests in `tests/test_backup_manager.py` with API and subprocess mocks.

## Dependencies
- Blocked by: WP-002
- Blocks: WP-004, WP-006, WP-008

## Acceptance Criteria
- [x] Failed stage is captured in `BackupResult.message`.
- [x] Helper pod cleanup executes reliably on success/failure paths.
- [x] Tests cover timeout, `kubectl` missing, and non-zero copy exit scenarios.
