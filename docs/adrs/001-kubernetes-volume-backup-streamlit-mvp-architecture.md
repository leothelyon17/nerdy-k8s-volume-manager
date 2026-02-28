# ADR-001: Kubernetes Volume Backup Streamlit MVP Architecture

## Metadata

| Field | Value |
|-------|-------|
| **Status** | Proposed |
| **Date** | 2026-02-23 |
| **Author(s)** | jeff, codex |
| **Reviewers** | TBD |
| **Work Package** | N/A |
| **Supersedes** | N/A |
| **Superseded By** | N/A |

## Summary

Build a Python 3.12 Streamlit application that authenticates to a Kubernetes cluster, inventories all PVCs, resolves the owning workload, and performs on-demand backups as `.tar.gz` files. The MVP stores backup artifacts in a local directory and writes backup outcomes to a local SQLite metadata database to track each PVC's latest successful backup timestamp. This prioritizes fast delivery and operator usability while preserving a clear path to object storage and snapshot-based backups.

## Context

### Problem Statement

Cluster operators need a lightweight UI to answer three operational questions quickly: what volumes exist, which app owns each volume, and when each volume was last backed up successfully. Existing generic backup tools are powerful but heavy for an MVP where immediate goals are interactive discovery and manual or batch volume backups to tar archives.

### Current State

This repository currently has no application code, no inventory mechanism, and no backup audit history. Backup operations are ad hoc and not centrally visible to operators.

### Requirements

| Requirement | Priority | Description |
|-------------|----------|-------------|
| REQ-1 | Must Have | Authenticate to Kubernetes via kubeconfig or in-cluster auth |
| REQ-2 | Must Have | List all PVCs and map each PVC to owning app/workload |
| REQ-3 | Must Have | Backup one or many PVCs as tar archives |
| REQ-4 | Must Have | Persist last successful backup timestamp per PVC |
| REQ-5 | Should Have | Show backup history with status and artifact path |
| REQ-6 | Nice to Have | Restore workflow and scheduled backups |

### Constraints

- **Budget**: Keep MVP near-zero additional cloud cost.
- **Timeline**: Deliver an initial runnable scaffold immediately.
- **Technical**: Python 3.12 and Streamlit; Kubernetes API as source of truth.
- **Compliance**: Do not write to application PVCs during backup; preserve auditable history.
- **Team**: Favor maintainable Python and minimal infra dependencies.

## Decision Drivers

1. **Fast path to usable MVP**: working UI + backup flow quickly.
2. **Operational clarity**: PVC-to-application ownership mapping and backup history.
3. **Low complexity**: avoid heavy backup stack during initial rollout.
4. **Upgrade path**: keep architecture extensible for snapshots/object storage later.

## Options Considered

### Option 1: Streamlit + Kubernetes API + ephemeral helper pod tar backup (chosen)

**Description**: Streamlit UI connects to cluster, discovers PVCs, launches a short-lived helper pod per selected PVC (read-only mount), creates tar archive inside pod, copies artifact with `kubectl cp`, stores metadata in SQLite.

**Implementation**:

```text
Streamlit UI
  -> Kubernetes API (PVCs, Pods, OwnerRefs)
  -> Helper Pod (PVC mounted read-only, tar -czf)
  -> Local backup directory (.tar.gz artifacts)
  -> SQLite metadata store (backup_history)
```

**Pros**:
- Fastest implementation path.
- Matches explicit tar-file requirement.
- Minimal infrastructure dependencies.
- Easy to understand and debug.

**Cons**:
- Requires `kubectl` in runtime environment.
- Local artifact storage is not durable across host loss.
- App-consistency depends on filesystem behavior unless workload is quiesced.

**Estimated Effort**: M

**Cost Implications**: Minimal; mostly transient pod CPU + local disk.

---

### Option 2: CSI VolumeSnapshot + export pipeline (fallback)

**Description**: Create VolumeSnapshots for selected PVCs, then export snapshot content to tar/object storage via dedicated jobs.

**Implementation**:

```text
Streamlit UI
  -> VolumeSnapshot CRDs
  -> Snapshot export job
  -> Object storage (S3/MinIO) + metadata DB
```

**Pros**:
- Better crash-consistency characteristics.
- Better scalability for larger datasets.
- Cleaner path to retention policies.

**Cons**:
- Requires CSI snapshot support and CRDs.
- More moving parts and operational complexity.
- Longer MVP lead time.

**Estimated Effort**: L

**Cost Implications**: Snapshot storage + object storage + compute jobs.

---

### Option 3: Integrate with Velero/restic immediately

**Description**: Streamlit serves as orchestration/reporting layer while backup execution delegates to Velero/restic.

**Pros**:
- Production-grade feature depth (retention, schedules, restore paths).
- Established ecosystem and community support.

**Cons**:
- Heavy integration for initial scope.
- Requires substantial configuration and cluster privileges.
- Harder to keep UX simple in first iteration.

**Estimated Effort**: L/XL

---

## Decision

### Chosen Option

**We will implement Option 1: Streamlit + Kubernetes API + ephemeral helper pod tar backup.**

### Rationale

Option 1 best satisfies MVP constraints: it directly provides cluster login, volume discovery, app ownership mapping, and single/multi-volume tar backups with minimal setup. It intentionally optimizes for speed and operational simplicity while preserving an explicit fallback path to Option 2 when consistency, scale, or durability requirements grow.

### Decision Matrix

| Criteria | Weight | Option 1 | Option 2 | Option 3 |
|----------|--------|----------|----------|----------|
| Time to MVP | 5 | 5 | 2 | 1 |
| Operational Simplicity | 4 | 4 | 2 | 2 |
| Data Consistency Potential | 4 | 2 | 5 | 4 |
| Long-term Scalability | 3 | 3 | 5 | 4 |
| Cost to Start | 3 | 5 | 3 | 2 |
| **Weighted Total** | | **66** | **58** | **46** |

## Consequences

### Positive

- Rapid delivery of an operator-facing tool with immediate value.
- Unified view of PVC ownership and backup recency.
- Explicit audit trail of success/failure events.

### Negative

- Local-only backup artifacts are not resilient by default; mitigate by adding object storage target.
- Tar backup can be crash-consistent rather than app-consistent; mitigate with optional pre/post hooks and/or quiesce logic.
- Per-PVC helper pod approach can be slower at high volume counts; mitigate with bounded concurrency and queueing.

### Risks

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Over-privileged service account | Med | High | Create least-privilege RBAC for PVC/pod read + pod create/delete in allowed namespaces |
| Backup artifact loss on app host failure | Med | High | Add pluggable artifact sink (S3/MinIO) and checksum verification |
| Incorrect owner mapping for complex controllers | Med | Med | Resolve owner refs recursively and show "Multiple/Unknown" explicitly in UI |

## Technical Details

### Architecture

```text
+---------------------------+
| Streamlit App (Python)    |
| - Auth config             |
| - PVC inventory UI        |
| - Backup orchestration UI |
+-------------+-------------+
              |
              v
+---------------------------+
| Kubernetes API            |
| - PVCs, Pods, OwnerRefs   |
| - Helper pod lifecycle    |
+-------------+-------------+
              |
              v
+---------------------------+
| Helper Pod (per backup)   |
| - PVC mount (read-only)   |
| - tar -czf /tmp/archive   |
+-------------+-------------+
              |
              v
+---------------------------+
| Backup Artifacts          |
| - local ./backups/*.tgz   |
+---------------------------+

+---------------------------+
| Metadata Store            |
| - SQLite backup_history   |
+---------------------------+
```

### AWS Services Involved

| Service | Purpose | Configuration |
|---------|---------|---------------|
| N/A | N/A | MVP is provider-agnostic Kubernetes + local storage |

### Database Changes

```sql
CREATE TABLE backup_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  pvc_uid TEXT NOT NULL,
  namespace TEXT NOT NULL,
  pvc_name TEXT NOT NULL,
  status TEXT NOT NULL,
  backup_path TEXT,
  checksum_sha256 TEXT,
  message TEXT,
  created_at TEXT NOT NULL
);
```

**Migration Strategy**: SQLite schema is bootstrapped automatically at app start.

### API Changes

**New Endpoints**: N/A (Streamlit UI app).

**Breaking Changes**: N/A.

### Configuration

```yaml
NKVM_BACKUP_DIR: ./backups
NKVM_METADATA_DB_PATH: ./data/backups.db
NKVM_HELPER_IMAGE: alpine:3.20
NKVM_HELPER_POD_TIMEOUT_SECONDS: 120
```

## Security Considerations

### Authentication & Authorization

Support kubeconfig-path, pasted kubeconfig, or in-cluster auth. Restrict the runtime service account to least privilege and namespace scope where possible.

### Data Protection

Backups are tar.gz artifacts on local disk in MVP; ensure host disk encryption and controlled filesystem permissions. Add at-rest encryption and object storage lifecycle in next phase.

### Compliance

Provide backup run history and immutable timestamps in metadata DB; add centralized log shipping in production.

### Threat Model

| Threat | Risk Level | Mitigation |
|--------|------------|------------|
| Credential leakage via pasted kubeconfig | Med | Keep kubeconfig temp files short-lived and protect host access |
| Malicious archive exfiltration | Med | Namespace allowlist + RBAC + audit logs |
| Helper pod image tampering | Med | Pin image digests and use trusted registry |

## Multi-Tenancy Impact

- **Tenant Data Isolation**: Depends on namespace-level RBAC and operator permissions.
- **Tenant-Specific Configuration**: Future: per-namespace allowlists and per-tenant backup destinations.
- **Cross-Tenant Operations**: Disabled by policy where required.
- **Tenant Onboarding/Offboarding**: Add namespace enrollment policy.

## Performance Considerations

### Expected Impact

| Metric | Current | Expected | Target |
|--------|---------|----------|--------|
| PVC discovery latency | N/A | < 5s (small clusters) | < 10s |
| Single backup startup latency | N/A | 5-20s (pod scheduling dependent) | < 30s |
| Backup throughput | N/A | PVC-size and node I/O bound | Track baseline |

### Scalability

MVP executes sequentially; add bounded concurrency controls for large batch backups.

### Monitoring

Track backup duration, bytes archived, success rate, and per-namespace failure rate.

## Cost Analysis

### Estimated Costs

| Component | Monthly Cost | Annual Cost | Notes |
|-----------|--------------|-------------|-------|
| Streamlit runtime host | Existing infra | Existing infra | Reuses operator host or small VM |
| Helper pod compute | Low | Low | Ephemeral, usage-based |
| Local storage | Variable | Variable | Depends on retained artifacts |
| **Total** | **Low** | **Low** | MVP avoids new managed services |

### Cost Optimization

Add retention policies, compression tuning, and optional offload to lower-cost object storage tiers.

## Implementation Plan

### Phases

| Phase | Description | Duration | Dependencies |
|-------|-------------|----------|--------------|
| 1 | Scaffold app, auth, PVC discovery, owner mapping | 2-3 days | None |
| 2 | Implement tar backup orchestration + metadata tracking | 2-3 days | Phase 1 |
| 3 | Hardening: RBAC, checksums, retry, docs, tests | 2-4 days | Phase 2 |

### Rollout Strategy

- [x] Feature flag implementation (MVP can be namespace-limited)
- [ ] Canary deployment in non-production cluster
- [ ] Gradual rollout by namespace allowlist
- [ ] Full deployment
- [ ] Cleanup/deprecation of manual backup scripts

### Rollback Plan

1. Disable backup action in UI.
2. Revert to prior manual backup process.
3. Preserve metadata DB and artifacts for audit trail.

## Testing Strategy

### Test Coverage

- [x] Unit tests for metadata store behavior
- [ ] Unit tests for owner-resolution edge cases
- [ ] Integration tests against KinD cluster
- [ ] Security tests for RBAC boundaries

### Acceptance Criteria

- [ ] User can authenticate and load PVC inventory.
- [ ] Each PVC displays owner workload or explicit unknown state.
- [ ] User can select one or many PVCs and produce tar artifacts.
- [ ] Last successful backup timestamp updates after successful run.

## Documentation Updates

- [x] Architecture documentation (this ADR)
- [ ] API reference (N/A)
- [ ] Runbook/operational guide
- [x] Developer guide (README scaffold)
- [ ] Restore procedure documentation

## Related Decisions

- None yet.

## References

- [Kubernetes Persistent Volumes](https://kubernetes.io/docs/concepts/storage/persistent-volumes/)
- [Kubernetes VolumeSnapshots](https://kubernetes.io/docs/concepts/storage/volume-snapshots/)
- [Streamlit Documentation](https://docs.streamlit.io/)

## Changelog

| Date | Author | Change |
|------|--------|--------|
| 2026-02-23 | jeff, codex | Initial draft |

---

## Approval

| Role | Name | Date | Approval |
|------|------|------|----------|
| Technical Lead | | | ☐ Approved |
| Security | | | ☐ Approved |
| Architecture | | | ☐ Approved |
| Product | | | ☐ Approved |
