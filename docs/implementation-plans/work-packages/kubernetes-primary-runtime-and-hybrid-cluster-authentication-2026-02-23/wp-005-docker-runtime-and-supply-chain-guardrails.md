# WP-005: Docker Runtime and Supply-Chain Guardrails

```yaml
WP_ID: WP-005
Domain: Infrastructure/Build
Priority: Medium
Estimated_Effort: 2-3 hours
Status: COMPLETE
Created_Date: 2026-02-23
Updated_Date: 2026-02-23
```

## Description
Harden container runtime and build guardrails so one image is safe and portable across Kubernetes and standalone operation.

## Deliverables
- [x] Harden image/runtime config in `Dockerfile` and `.dockerignore`.
- [x] Verify non-root runtime, required binaries, and deterministic env defaults.
- [x] Add container run/verification steps to `README.md` and `deploy/k8s/README.md`.

## Dependencies
- Blocked by: None
- Blocks: WP-006, WP-007

## Acceptance Criteria
- [x] Image includes `kubectl` and Streamlit runtime with non-root user.
- [x] Build reproducibility inputs are documented (base image + kubectl version pin).
- [x] Standalone run flow remains functional with mounted kubeconfig.
- [x] No sensitive local artifacts are included in build context.

## Execution Notes (2026-02-23)
- Hardened `Dockerfile` with explicit build inputs (`PYTHON_IMAGE`, `KUBECTL_VERSION`), deterministic runtime env defaults, fixed non-root UID/GID, and build-time `kubectl` client verification.
- Expanded `.dockerignore` with sensitive local artifact exclusions (`.env*`, kubeconfig patterns, SSH/cert key material, local DB files) to prevent accidental context leakage.
- Added runtime verification commands in:
  - `README.md`
  - `deploy/k8s/README.md`
- Added regression tests in `tests/test_container_guardrails.py` to enforce Docker build/runtime/documentation guardrails.
