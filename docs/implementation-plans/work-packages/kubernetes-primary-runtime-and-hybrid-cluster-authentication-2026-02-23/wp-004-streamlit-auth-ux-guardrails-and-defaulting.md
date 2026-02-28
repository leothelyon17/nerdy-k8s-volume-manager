# WP-004: Streamlit Auth UX Guardrails and Defaulting

```yaml
WP_ID: WP-004
Domain: UI Layer
Priority: Medium
Estimated_Effort: 2-3 hours
Status: COMPLETE
Created_Date: 2026-02-23
```

## Description
Refine auth UX defaults and input validation so operators can reliably switch between in-cluster and remote modes.

## Deliverables
- [x] Refine auth-mode defaults and validation messaging in `src/nerdy_k8s_volume_manager/app.py`.
- [x] Expand UI-logic tests in `tests/test_app.py` for mode transitions and invalid input paths.
- [x] Ensure auth guidance text is consistent with `docs/operations/authentication-methods.md`.

## Dependencies
- Blocked by: WP-003
- Blocks: WP-007

## Acceptance Criteria
- [x] In-cluster environments default to `In-cluster ServiceAccount` flow predictably.
- [x] Invalid kubeconfig path/paste inputs are blocked before connection attempts.
- [x] Operator messaging clearly differentiates in-cluster vs remote setup.
- [x] Test coverage protects against regression in default mode selection.
