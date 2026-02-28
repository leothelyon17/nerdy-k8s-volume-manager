# Implementation Plan: Kubernetes-Primary Runtime and Hybrid Cluster Authentication

**Date Created:** 2026-02-23  
**Project Owner:** Jeff  
**Target Completion:** 2026-02-27  
**Status:** DRAFT

---

## 1. Executive Summary

This plan executes ADR-002 by making Kubernetes the primary runtime for `nerdy-k8s-volume-manager` while preserving hybrid authentication (in-cluster ServiceAccount, kubeconfig path, pasted kubeconfig). The repository already contains the core artifacts; this plan focuses on hardening, automated validation, and release readiness so the operating model is reliable in real clusters.

---

## 2. Requirements Analysis

### Must-Have Requirements (ADR-002)
- REQ-1: Kubernetes deployment is the primary documented and operational path.
- REQ-2: In-cluster ServiceAccount authentication is first-class and reliable.
- REQ-3: Remote cluster API endpoint support remains available.
- REQ-4: Docker image runs in Kubernetes and standalone.

### Should-Have Requirements (ADR-002)
- REQ-5: Clear authentication examples for all supported methods.
- REQ-6: Minimal disruption to existing UI and backup workflows.

### Current State Snapshot (2026-02-23)
- Present:
  - Docker image and runtime defaults (`Dockerfile`, `.dockerignore`).
  - Kubernetes app manifests (`deploy/k8s/app/`).
  - RBAC package (`deploy/k8s/rbac/`).
  - Hybrid auth UX and connection logic (`src/nerdy_k8s_volume_manager/app.py`).
  - In-cluster `kubectl cp` kubeconfig generation (`src/nerdy_k8s_volume_manager/backup.py`).
  - Auth and security docs (`docs/operations/authentication-methods.md`, `docs/operations/security-baseline.md`).
- Remaining gaps to close:
  - End-to-end Kubernetes runtime validation for both in-cluster and remote-secret auth paths.
  - Manifest and RBAC policy hardening checks as release gates.
  - Consolidated deployment/release acceptance checklist tied to ADR-002.

### Scope

**In Scope**
- Harden Kubernetes runtime manifests and RBAC for the ADR-002 model.
- Validate hybrid auth behavior with unit + integration coverage.
- Strengthen image/runtime guardrails and deployment documentation.

**Out of Scope**
- Replacing `kubectl cp` transport with alternate data plane.
- Introducing separate app binaries for in-cluster vs remote-only operation.
- Major UI redesign unrelated to authentication/runtime flow.

---

## 3. Domain Mapping

Feature: ADR-002 execution hardening

- Data Layer
  - No new persistent model/table required.
  - Validate existing metadata path behavior in container and in-cluster mount scenarios.
- Service Layer
  - Backup copy reliability with generated in-cluster kubeconfig.
  - Correct context/kubeconfig behavior for remote clusters.
- UI Layer
  - Deterministic auth mode defaults and validation messaging.
  - Low-friction operator path for in-cluster default and remote override.
- Infrastructure
  - Kubernetes app manifests and RBAC are primary deployment artifacts.
  - Runtime/image guardrails for production-safe defaults.
- Event Layer
  - Not required for ADR-002.
- Scheduled Jobs
  - Not required for ADR-002.
- Operations/Documentation
  - Explicit runbooks and auth method instructions for operators.
- Quality
  - KinD/integration verification and release gates.

Cross-cutting concerns:
- Security: least privilege RBAC, secret handling, auth mode hygiene.
- Observability: actionable failure messages for auth/copy/deploy failures.
- Portability: one image and one app path across Kubernetes and standalone.

---

## 4. Work Package Breakdown

Canonical work package files are split out under:

`docs/implementation-plans/work-packages/kubernetes-primary-runtime-and-hybrid-cluster-authentication-2026-02-23/`

- `WP-001`: `docs/implementation-plans/work-packages/kubernetes-primary-runtime-and-hybrid-cluster-authentication-2026-02-23/wp-001-kubernetes-app-manifest-hardening.md`
- `WP-002`: `docs/implementation-plans/work-packages/kubernetes-primary-runtime-and-hybrid-cluster-authentication-2026-02-23/wp-002-rbac-least-privilege-validation-and-binding-workflow.md`
- `WP-003`: `docs/implementation-plans/work-packages/kubernetes-primary-runtime-and-hybrid-cluster-authentication-2026-02-23/wp-003-hybrid-auth-and-in-cluster-copy-path-reliability.md`
- `WP-004`: `docs/implementation-plans/work-packages/kubernetes-primary-runtime-and-hybrid-cluster-authentication-2026-02-23/wp-004-streamlit-auth-ux-guardrails-and-defaulting.md`
- `WP-005`: `docs/implementation-plans/work-packages/kubernetes-primary-runtime-and-hybrid-cluster-authentication-2026-02-23/wp-005-docker-runtime-and-supply-chain-guardrails.md`
- `WP-006`: `docs/implementation-plans/work-packages/kubernetes-primary-runtime-and-hybrid-cluster-authentication-2026-02-23/wp-006-kind-integration-validation-for-kubernetes-primary-hybrid-auth.md`
- `WP-007`: `docs/implementation-plans/work-packages/kubernetes-primary-runtime-and-hybrid-cluster-authentication-2026-02-23/wp-007-documentation-and-release-readiness-closure.md`

### Phase 1: Foundation Hardening
- WP-001: Kubernetes app manifests.
- WP-002: RBAC least-privilege and onboarding workflow.
- WP-003: Hybrid auth and in-cluster copy-path reliability.
- WP-005: Docker/runtime guardrails.

### Phase 2: Experience and Validation
- WP-004: Streamlit auth UX guardrails and defaults.
- WP-006: KinD integration validation for both auth modes.

### Phase 3: Release Closure
- WP-007: Documentation and release readiness closure.

---

## 5. Dependency Graph

```text
WP-001 ─┐
WP-002 ─┼───────────────────┐
WP-003 ─┬─> WP-004 ─────────┤
      └───────────────┤
WP-005 ─────────────────────┤
                      v
                    WP-006
                      v
                    WP-007
```

### Critical Path
- WP-003 -> WP-006 -> WP-007

### Parallel Opportunities
- Start immediately in parallel: WP-001, WP-002, WP-003, WP-005
- Start after WP-003: WP-004
- Start after WP-001/WP-002/WP-003/WP-005: WP-006
- Start after all prior WPs: WP-007

---

## 6. Timeline and Effort

### Effort Estimate
- WP-001: 3.5h
- WP-002: 3.5h
- WP-003: 4.5h
- WP-004: 2.5h
- WP-005: 2.5h
- WP-006: 5.0h
- WP-007: 3.5h
- **Total Effort:** 25.0h

### Duration Estimate
- Critical path (best-case parallel execution): ~13.0h
  - WP-003 (4.5h) + WP-006 (5.0h) + WP-007 (3.5h)
- Aggressive: ~16h (critical path x1.2)
- Conservative: ~37.5h (total effort x1.5)
- **Realistic single-engineer duration (6 productive h/day): 3-5 business days**

### Suggested Sequence
1. Day 1: WP-001, WP-002, WP-003, WP-005 (parallel where possible)
2. Day 2: WP-004 + begin WP-006
3. Day 3: Complete WP-006
4. Day 4-5: WP-007, final verification, release gate

---

## 7. Risk Assessment

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| RBAC over-permission during namespace onboarding | Medium | High | Keep discovery/runtime roles split; add explicit review checklist in WP-002/WP-007 |
| In-cluster `kubectl cp` regressions after auth/path changes | Medium | High | Stage-specific tests in WP-003 and integration validation in WP-006 |
| Remote kubeconfig secret mishandling by operators | Medium | High | Enforce secret-mount documentation and least-access guidance in WP-007 |
| Manifest drift between docs and actual YAML | Medium | Medium | Add dry-run and rollout verification commands in WP-001/WP-007 |
| Integration test instability in KinD | Low | Medium | Deterministic setup/teardown and explicit troubleshooting in WP-006 |

---

## 8. Success Criteria

- [ ] All seven work packages completed with acceptance criteria satisfied.
- [ ] Hybrid auth modes validated in unit and integration suites.
- [ ] Kubernetes deployment path is clearly primary across docs and examples.
- [ ] Security baseline and RBAC onboarding are operationally usable.
- [ ] ADR-002 requirements are traceable and verified.

---

## 9. Next Steps

1. Approve this plan and assign owners per work package.
2. Execute WP-001, WP-002, WP-003, and WP-005 first (parallel lane).
3. Run WP-006 integration validation as the release gate.
4. Complete WP-007 and tag ADR-002 execution complete.
