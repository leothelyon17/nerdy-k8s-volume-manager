# WP-008: Optional Artifact Sink Abstraction (S3/MinIO Ready)

```yaml
WP_ID: WP-008
Domain: Service Layer
Priority: Low
Estimated_Effort: 5 hours
Status: DEFINED
Created_Date: 2026-02-23
```

## Description
Introduce storage abstraction so local filesystem remains default while enabling optional object-storage sink.

## Deliverables
- [ ] Add sink interface and local sink implementation under `src/nerdy_k8s_volume_manager/storage.py`.
- [ ] Add optional S3-compatible sink stub/config contract.
- [ ] Add unit tests in `tests/test_storage.py`.

## Dependencies
- Blocked by: WP-001, WP-003
- Blocks: None (optional enhancement)

## Acceptance Criteria
- [ ] Existing local backup behavior remains default and backward-compatible.
- [ ] Sink interface supports future offload without touching UI workflow.
- [ ] Unit tests validate interface contract and local sink behavior.
