# Implementation Plan: Kubernetes Volume Backup Streamlit MVP

**Date Created:** 2026-02-23  
**Project Owner:** Jeff  
**Target Completion:** 2026-03-02  
**Status:** PLANNING

---

## Project Overview

### Goal
Deliver a production-usable MVP for Kubernetes PVC inventory and on-demand `.tar.gz` backups in Streamlit, including backup history persistence, RBAC hardening, and operational documentation.

### Success Criteria
- [ ] Operators can authenticate and inventory PVCs with owner mapping in <10 seconds for small clusters.
- [ ] Single and multi-PVC backups produce valid archives with persisted success/failure history.
- [ ] Security and operations baseline is in place (least-privilege RBAC, checksums, runbook, restore procedure).
- [ ] Test suite covers unit and KinD integration scenarios with >=80% coverage on core modules.

### Scope

**In Scope:**
- Harden existing Streamlit, Kubernetes integration, backup orchestration, and SQLite metadata modules.
- Add security controls (RBAC manifests/policy guidance), reliability controls, and test coverage.
- Document deployment, operations, rollback, and restore procedure.

**Out of Scope:**
- Full Velero/restic integration.
- Production scheduler/orchestrator for recurring backups.
- Multi-cloud object storage as mandatory path (kept as optional WP).

### Constraints
- Technical: Python 3.12, Streamlit UI, Kubernetes API + `kubectl cp`, local artifact storage for MVP.
- Timeline: One-week implementation target for hardened MVP.
- Resources: Assumed one engineer, ~6 productive hours/day.
- Compliance: Read-only PVC mounts during backup, auditable backup records.

---

## Requirements Analysis

### Must-Have (from ADR-001)
- REQ-1: Authenticate via kubeconfig path, pasted kubeconfig, or in-cluster auth.
- REQ-2: List all PVCs and map each PVC to owning workload.
- REQ-3: Backup one or many PVCs as `.tar.gz`.
- REQ-4: Persist last successful backup timestamp per PVC.

### Should-Have
- REQ-5: Show backup history with status and artifact path.

### Nice-to-Have
- REQ-6: Restore workflow and scheduled backups.

### Current State Snapshot (codebase)
- Implemented: Streamlit app scaffold, Kubernetes discovery, helper-pod backup flow, SQLite metadata store, basic unit tests.
- Gaps to close: integration tests, stronger error handling/timeouts, RBAC artifact definitions, restore/ops docs, edge-case owner mapping coverage.

---

## Domain Mapping

Feature: Kubernetes Volume Backup Streamlit MVP Hardening

- Data Layer
  - SQLite schema/index verification, result query ergonomics, retention-ready query patterns.
- Service Layer
  - Kubernetes inventory and owner resolution reliability.
  - Backup orchestration robustness (helper pod lifecycle, tar validation, copy failure handling).
- UI Layer
  - Streamlit operator UX for filtering, selection, progress, and result clarity.
- Infrastructure/Security
  - RBAC manifests, namespace policy guidance, helper image pinning controls.
- Quality/Operations
  - KinD integration tests, runbook, restore procedure, release checklist.
- Optional Extension
  - Pluggable artifact sink abstraction for S3/MinIO.

Cross-cutting concerns:
- Security: least privilege, credential handling, namespace restrictions.
- Observability: structured logs, duration/bytes metrics, failure reason surfacing.
- Performance: bounded batch concurrency and timeout controls.

---

## Work Package Breakdown

Canonical work package files are split out under:

`docs/implementation-plans/work-packages/kubernetes-volume-backup-streamlit-mvp-2026-02-23/`

- `WP-001`: `docs/implementation-plans/work-packages/kubernetes-volume-backup-streamlit-mvp-2026-02-23/wp-001-metadata-layer-hardening-and-query-utilities.md`
- `WP-002`: `docs/implementation-plans/work-packages/kubernetes-volume-backup-streamlit-mvp-2026-02-23/wp-002-pvc-discovery-and-owner-resolution-edge-cases.md`
- `WP-003`: `docs/implementation-plans/work-packages/kubernetes-volume-backup-streamlit-mvp-2026-02-23/wp-003-backup-orchestration-robustness.md`
- `WP-004`: `docs/implementation-plans/work-packages/kubernetes-volume-backup-streamlit-mvp-2026-02-23/wp-004-streamlit-ux-completion-for-batch-backups.md`
- `WP-005`: `docs/implementation-plans/work-packages/kubernetes-volume-backup-streamlit-mvp-2026-02-23/wp-005-rbac-and-runtime-security-controls.md`
- `WP-006`: `docs/implementation-plans/work-packages/kubernetes-volume-backup-streamlit-mvp-2026-02-23/wp-006-kind-integration-test-harness-and-ci-validation.md`
- `WP-007`: `docs/implementation-plans/work-packages/kubernetes-volume-backup-streamlit-mvp-2026-02-23/wp-007-operations-runbook-and-restore-procedure.md`
- `WP-008`: `docs/implementation-plans/work-packages/kubernetes-volume-backup-streamlit-mvp-2026-02-23/wp-008-optional-artifact-sink-abstraction.md`

### Phase 1: Core Reliability

#### WP-001: Metadata Layer Hardening and Query Utilities

```yaml
Domain: Data Layer
Priority: High
Estimated_Effort: 4 hours
Status: DEFINED
```

**Description:**  
Strengthen SQLite metadata behavior for reliable history and latest-success lookups under repeated backup operations.

**Deliverables:**
- [ ] Update `src/nerdy_k8s_volume_manager/metadata.py` with stricter query/ordering behavior and retention-ready helper methods.
- [ ] Add metadata edge-case tests in `tests/test_metadata.py` (same timestamp ordering, mixed statuses, empty history).
- [ ] Document metadata schema and query contract in `README.md`.

**Dependencies:**
- Blocked by: None
- Blocks: WP-004, WP-006, WP-008

**Acceptance Criteria:**
- [ ] `get_last_success_map()` and `get_recent_results()` are deterministic.
- [ ] New tests pass and metadata module coverage >=80%.
- [ ] No schema regression against existing `backup_history` table.

---

#### WP-002: PVC Discovery and Owner-Resolution Edge Case Coverage

```yaml
Domain: Service Layer
Priority: High
Estimated_Effort: 5 hours
Status: DEFINED
```

**Description:**  
Harden PVC inventory and owner resolution for ReplicaSet/Deployment, Job/CronJob, and ambiguous/missing owner-reference scenarios.

**Deliverables:**
- [ ] Improve owner-resolution behavior in `src/nerdy_k8s_volume_manager/k8s.py` for unresolved/multi-owner presentation.
- [ ] Add unit tests for owner chain recursion and missing API objects in `tests/test_k8s.py`.
- [ ] Add guardrails for large namespace scans (timeouts and error messaging strategy).

**Dependencies:**
- Blocked by: None
- Blocks: WP-004, WP-005, WP-006

**Acceptance Criteria:**
- [ ] Owner mapping returns deterministic `Unknown`/`Multiple[...]` states when ambiguous.
- [ ] Discovery succeeds/fails with actionable errors (no silent failure paths).
- [ ] Unit tests cover controller resolution branches and ApiException handling.

---

#### WP-003: Backup Orchestration Robustness

```yaml
Domain: Service Layer
Priority: High
Estimated_Effort: 5 hours
Status: DEFINED
```

**Description:**  
Improve backup execution reliability around helper pod lifecycle, tar command execution, timeout handling, and archive validation.

**Deliverables:**
- [ ] Refine backup flow in `src/nerdy_k8s_volume_manager/backup.py` (clear failure reasons for create/wait/exec/copy/checksum stages).
- [ ] Add bounded retry logic for transient pod startup failures.
- [ ] Add unit tests in `tests/test_backup_manager.py` with API and subprocess mocks.

**Dependencies:**
- Blocked by: WP-002
- Blocks: WP-004, WP-006, WP-008

**Acceptance Criteria:**
- [ ] Failed stage is captured in `BackupResult.message`.
- [ ] Helper pod cleanup executes reliably on success/failure paths.
- [ ] Tests cover timeout, `kubectl` missing, and non-zero copy exit scenarios.

---

### Phase 2: Operator UX and Security Baseline

#### WP-004: Streamlit UX Completion for Batch Backup Operations

```yaml
Domain: UI Layer
Priority: High
Estimated_Effort: 4 hours
Status: DEFINED
```

**Description:**  
Complete MVP operator workflow clarity for inventory refresh, backup selection, progress visibility, and outcome inspection.

**Deliverables:**
- [ ] Update `src/nerdy_k8s_volume_manager/app.py` to expose clearer per-step status and validation errors.
- [ ] Add configurable batch controls (sequential now, concurrency-ready config surface).
- [ ] Add UI regression tests (where practical) or logic-level tests for row/result mapping helpers.

**Dependencies:**
- Blocked by: WP-001, WP-002, WP-003
- Blocks: WP-006, WP-007

**Acceptance Criteria:**
- [ ] User can complete connect -> discover -> select -> backup -> review history without dead-end states.
- [ ] Failed backups are visible with actionable message text.
- [ ] UI logic helpers have test coverage for core transformations.

---

#### WP-005: RBAC and Runtime Security Controls

```yaml
Domain: Infrastructure
Priority: High
Estimated_Effort: 4 hours
Status: DEFINED
```

**Description:**  
Define and document least-privilege permissions needed for discovery and helper-pod backup execution.

**Deliverables:**
- [ ] Add Kubernetes RBAC manifests in `deploy/k8s/rbac/` (`ServiceAccount`, `Role`/`ClusterRole`, bindings).
- [ ] Add namespace-allowlist operational guidance in `docs/operations/security-baseline.md`.
- [ ] Add helper image pinning/config recommendations to `README.md`.

**Dependencies:**
- Blocked by: WP-002
- Blocks: WP-006, WP-007

**Acceptance Criteria:**
- [ ] RBAC scope explicitly lists required verbs/resources only.
- [ ] Security guidance covers kubeconfig handling and host file-permission baseline.
- [ ] Docs provide production hardening checklist.

---

### Phase 3: Validation and Operations

#### WP-006: KinD Integration Test Harness and CI Validation Path

```yaml
Domain: Quality/Infrastructure
Priority: High
Estimated_Effort: 6 hours
Status: DEFINED
```

**Description:**  
Add integration tests against a disposable KinD cluster to validate discovery and backup behavior end-to-end.

**Deliverables:**
- [ ] Create KinD test scaffolding under `tests/integration/` with fixture setup/teardown.
- [ ] Add integration tests for PVC discovery and backup execution path (smoke level).
- [ ] Add CI execution instructions (or workflow file if repo CI is desired now).

**Dependencies:**
- Blocked by: WP-001, WP-003, WP-004, WP-005
- Blocks: WP-007

**Acceptance Criteria:**
- [ ] Integration suite runs locally with documented prerequisites.
- [ ] At least one full-path test verifies artifact creation + metadata persistence.
- [ ] Failure diagnostics are actionable (logs surfaced in test output).

---

#### WP-007: Operations Runbook and Restore Procedure

```yaml
Domain: Operations
Priority: Medium
Estimated_Effort: 3 hours
Status: DEFINED
```

**Description:**  
Produce operator-ready runbook and restore guide to close ADR documentation gaps.

**Deliverables:**
- [ ] Add `docs/runbooks/mvp-operations.md` (startup, backup execution, troubleshooting, rollback).
- [ ] Add `docs/runbooks/restore-procedure.md` with step-by-step validation flow.
- [ ] Update `README.md` with links and release readiness checklist.

**Dependencies:**
- Blocked by: WP-004, WP-005, WP-006
- Blocks: None

**Acceptance Criteria:**
- [ ] Runbook includes incident scenarios and recovery actions.
- [ ] Restore procedure includes verification and failure handling.
- [ ] Documentation references match implemented commands/paths.

---

#### WP-008: Optional Artifact Sink Abstraction (S3/MinIO Ready)

```yaml
Domain: Service Layer
Priority: Low
Estimated_Effort: 5 hours
Status: DEFINED
```

**Description:**  
Introduce storage abstraction so local filesystem remains default while enabling optional object-storage sink.

**Deliverables:**
- [ ] Add sink interface and local sink implementation under `src/nerdy_k8s_volume_manager/storage.py`.
- [ ] Add optional S3-compatible sink stub/config contract.
- [ ] Add unit tests in `tests/test_storage.py`.

**Dependencies:**
- Blocked by: WP-001, WP-003
- Blocks: None (optional enhancement)

**Acceptance Criteria:**
- [ ] Existing local backup behavior remains default and backward-compatible.
- [ ] Sink interface supports future offload without touching UI workflow.
- [ ] Unit tests validate interface contract and local sink behavior.

---

## Dependency Graph

```text
WP-001 (Metadata) ───────────────┐
                                 ├──> WP-004 (Streamlit UX) ──┐
WP-002 (K8s Discovery) -> WP-003 ┘                            │
                         (Backup Robustness) ------------------┤
                                                               ├──> WP-006 (KinD Integration) -> WP-007 (Runbook/Restore)
WP-005 (RBAC/Security) ----------------------------------------┘

WP-008 (Artifact Sink, optional) depends on WP-001 + WP-003 and can run in parallel with WP-006/WP-007.
```

Critical path (MVP): `WP-002 -> WP-003 -> WP-004 -> WP-006 -> WP-007`

---

## Execution Timeline

Assumption: 6 productive engineering hours/day.

| Phase | WPs | Duration (hours) | Calendar Estimate |
|-------|-----|------------------|-------------------|
| Phase 1: Core Reliability | WP-001, WP-002, WP-003 | 14 | 2.5 days |
| Phase 2: UX + Security | WP-004, WP-005 | 8 | 1.5 days |
| Phase 3: Validation + Ops | WP-006, WP-007 | 9 | 1.5 days |
| Optional Extension | WP-008 | 5 | +1 day |
| **Total (MVP required)** | **WP-001..WP-007** | **31** | **~5.5 days** |
| **Total (with optional WP-008)** | **WP-001..WP-008** | **36** | **~6.5 days** |

Estimate bands:
- Aggressive (good flow, minimal rework): 26-30 hours
- Realistic: 31-36 hours
- Conservative (integration issues): 40-45 hours

Parallel opportunities:
- WP-001 and WP-002 can start immediately in parallel.
- WP-005 can run in parallel once WP-002 is stable.
- WP-008 can run in parallel after WP-003 if prioritized.

---

## Risk Management

### Risk 1: Over-privileged cluster permissions
- Probability: Medium
- Impact: High
- Mitigation: ship minimal RBAC manifests, namespace allowlist guidance, explicit required verbs table.
- Contingency: feature-flag backup actions and restrict discovery scope until RBAC corrected.
- Owner: Engineering

### Risk 2: Backup inconsistency for write-heavy workloads
- Probability: Medium
- Impact: High
- Mitigation: document crash-consistent behavior, add pre/post hook placeholders, recommend application quiesce process.
- Contingency: mark affected backups as non-validated and require restore test before production reliance.
- Owner: Engineering + Operations

### Risk 3: `kubectl cp` / helper pod transient failures
- Probability: Medium
- Impact: Medium
- Mitigation: retries for transient states, explicit stage-based errors, timeout tuning.
- Contingency: manual retry workflow and troubleshooting playbook in runbook.
- Owner: Engineering

### Risk 4: Owner mapping ambiguity for complex controllers
- Probability: Medium
- Impact: Medium
- Mitigation: recursive owner resolution tests and explicit `Unknown`/`Multiple[...]` UI state.
- Contingency: provide per-PVC raw owner refs in debug mode for operator validation.
- Owner: Engineering

### Risk 5: Integration test flakiness in KinD
- Probability: Medium
- Impact: Medium
- Mitigation: deterministic fixtures, bounded timeouts, isolated namespace per test.
- Contingency: keep smoke integration tests in required gate; run broader suite nightly.
- Owner: Engineering

### Risk 6: Local disk exhaustion from retained artifacts
- Probability: Medium
- Impact: High
- Mitigation: retention guidance, free-space preflight checks, optional sink abstraction (WP-008).
- Contingency: emergency cleanup procedure and artifact offload runbook.
- Owner: Operations

---

## Success Criteria and Exit Gates

- [ ] All required WPs (WP-001..WP-007) completed with acceptance criteria met.
- [ ] `pytest` passes for unit + integration paths.
- [ ] Lint/format checks pass (`ruff check .` and `ruff format --check .` when configured).
- [ ] Documentation complete: README, security baseline, operations runbook, restore procedure.
- [ ] Demo script validates end-to-end flow in a test cluster.

---

## Assumptions and Open Questions

Assumptions:
- Single engineer executing plan unless otherwise assigned.
- KinD is acceptable as the integration baseline.
- Local filesystem remains the default artifact sink for MVP.

Open questions:
- Should WP-008 (object storage-ready sink abstraction) be included in MVP or scheduled immediately after?
- Is namespace scope fixed (single namespace) or operator-configurable for production rollout?

---

## Next Steps

1. Approve scope split: MVP required (WP-001..WP-007) vs optional (WP-008).
2. Start execution with WP-001 and WP-002 in parallel.
3. Re-estimate after WP-003 based on backup failure patterns seen in test cluster.
