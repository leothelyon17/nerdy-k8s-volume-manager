# WP-001: Kubernetes App Manifest Hardening

```yaml
WP_ID: WP-001
Domain: Infrastructure
Priority: High
Estimated_Effort: 3-4 hours
Status: PARTIAL
Created_Date: 2026-02-23
Updated_Date: 2026-02-23
```

## Description
Validate and harden Kubernetes application manifests so the ADR-002 deployment model is safe, repeatable, and easy to verify.

## Deliverables
- [x] Validate and harden:
  - `deploy/k8s/app/deployment.yaml`
  - `deploy/k8s/app/service.yaml`
  - `deploy/k8s/app/persistentvolumeclaim.yaml`
  - `deploy/k8s/app/kustomization.yaml`
- [x] Add explicit manifest validation steps to `deploy/k8s/README.md` (`kubectl apply --dry-run=server`, rollout checks).
- [x] Ensure optional remote kubeconfig mount semantics remain safe and clearly optional.

## Dependencies
- Blocked by: None
- Blocks: WP-006, WP-007

## Acceptance Criteria
- [ ] App manifests apply cleanly in dry-run and live namespace tests. (Pending: requires `kubectl` + cluster access in validation environment.)
- [x] Deployment defaults to `NKVM_DEFAULT_AUTH_MODE=in-cluster`.
- [x] PVC/data mount paths align with runtime env variables and docs.
- [x] No privileged container settings introduced.

## Technical Notes
- If additional manifest patterns are needed, reference examples from `AHEAD-Labs/net-automation-k8s-lab`.

## Execution Notes (2026-02-23)
- Added manifest hardening controls:
  - Pod `seccompProfile: RuntimeDefault`
  - Container `allowPrivilegeEscalation: false`, `privileged: false`, capabilities drop `ALL`
  - Secret-backed remote kubeconfig volume `defaultMode: 0400`, still `optional: true`
  - PVC `volumeMode: Filesystem`
  - Service `appProtocol: http`
  - Kustomization namespace pinning (`nerdy-k8s-volume-manager`)
- Added explicit dry-run, apply, and rollout verification commands to `deploy/k8s/README.md`.
- Added regression tests in `tests/test_k8s_manifests.py` to enforce auth defaulting, path/mount alignment, optional remote mount behavior, and non-privileged runtime settings.
