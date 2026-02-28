# WP-005: RBAC and Runtime Security Controls

```yaml
WP_ID: WP-005
Domain: Infrastructure
Priority: High
Estimated_Effort: 4 hours
Status: COMPLETE
Created_Date: 2026-02-23
```

## Description
Define and document least-privilege permissions needed for discovery and helper-pod backup execution.

## Deliverables
- [x] Add Kubernetes RBAC manifests in `deploy/k8s/rbac/` (`ServiceAccount`, `Role`/`ClusterRole`, bindings).
- [x] Add namespace-allowlist operational guidance in `docs/operations/security-baseline.md`.
- [x] Add helper image pinning/config recommendations to `README.md`.

## Dependencies
- Blocked by: WP-002
- Blocks: WP-006, WP-007

## Acceptance Criteria
- [x] RBAC scope explicitly lists required verbs/resources only.
- [x] Security guidance covers kubeconfig handling and host file-permission baseline.
- [x] Docs provide production hardening checklist.
