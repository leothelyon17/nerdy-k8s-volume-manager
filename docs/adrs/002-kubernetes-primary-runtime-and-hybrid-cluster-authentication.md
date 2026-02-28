# ADR-002: Kubernetes-Primary Runtime and Hybrid Cluster Authentication

## Metadata

| Field | Value |
|-------|-------|
| **Status** | Accepted |
| **Date** | 2026-02-23 |
| **Author(s)** | jeff, codex |
| **Reviewers** | TBD |
| **Work Package** | N/A |
| **Supersedes** | N/A |
| **Superseded By** | N/A |

## Summary

Adopt Kubernetes as the primary runtime and deployment model for `nerdy-k8s-volume-manager`, while retaining flexible authentication for both same-cluster and remote-cluster operations. The runtime will use in-cluster ServiceAccount auth by default, and also support kubeconfig-path and pasted-kubeconfig flows for remote API endpoints. A first-class Docker image is required so the same artifact runs in Kubernetes and standalone environments.

## Context

### Problem Statement

The MVP architecture supports multiple auth methods, but deployment guidance was local-first and lacked a concrete Kubernetes application deployment package and container build artifact. Operators need a deterministic in-cluster deployment pattern that can safely back up PVCs in its own cluster with ServiceAccount credentials, and still connect to external clusters when needed.

### Current State

- Existing RBAC manifests are present.
- App supports auth modes: kubeconfig path, pasted kubeconfig, in-cluster auth.
- Backup copy depends on `kubectl cp`, which needs explicit kubeconfig handling in in-cluster runtimes.
- No committed Dockerfile or primary Kubernetes app manifests existed.

### Requirements

| Requirement | Priority | Description |
|-------------|----------|-------------|
| REQ-1 | Must Have | Kubernetes deployment is the primary documented/operational path |
| REQ-2 | Must Have | In-cluster ServiceAccount auth is first-class and reliable |
| REQ-3 | Must Have | Remote cluster API endpoint support remains available |
| REQ-4 | Must Have | Docker image runs both in Kubernetes and standalone |
| REQ-5 | Should Have | Clear authentication examples for all supported methods |
| REQ-6 | Should Have | Minimal disruption to existing UI and backup workflows |

### Constraints

- **Budget**: Keep low operational overhead and reuse existing MVP architecture.
- **Timeline**: Implement immediately without redesigning full backup engine.
- **Technical**: Maintain Streamlit + Kubernetes Python client + helper pod flow.
- **Compliance**: Preserve least-privilege RBAC and auditable backup history.
- **Team**: Keep implementation maintainable in pure Python and YAML.

## Decision Drivers

1. **Operational default clarity**: one primary way to deploy and run in production.
2. **Auth flexibility**: same app must support same-cluster and remote clusters.
3. **Runtime reliability**: avoid in-cluster auth gaps in backup copy path.
4. **Artifact portability**: one Docker image across environments.
5. **Security posture**: maintain least privilege and explicit secret handling.

## Options Considered

### Option 1: Kubernetes-primary runtime with hybrid auth modes (chosen)

**Description**: Ship a Docker image, deploy app in Kubernetes by default with ServiceAccount auth, keep kubeconfig-path and pasted-kubeconfig auth for remote access, and update copy flow for in-cluster operation.

**Implementation**:
```text
Kubernetes Deployment (primary)
  -> ServiceAccount + RBAC
  -> Streamlit app container + kubectl
  -> PVC-backed runtime storage
  -> Optional Secret-mounted remote kubeconfig

Standalone Docker (fallback)
  -> Same image
  -> Mounted kubeconfig + local data mounts
```

**Pros**:
- Aligns runtime with Kubernetes-native operations.
- Keeps remote cluster support without separate binaries.
- Single artifact and docs for both deployment styles.

**Cons**:
- Requires container image lifecycle and registry management.
- `kubectl` must stay in the image to support `kubectl cp`.
- Operators must manage namespace-scoped RoleBindings explicitly.

**Estimated Effort**: M

**Cost Implications**: Low; one app pod + transient helper pods + persistent storage.

---

### Option 2: Keep local-first runtime, document Kubernetes as optional

**Description**: Continue local Python execution as primary path and provide optional Kubernetes examples.

**Pros**:
- Lowest immediate implementation effort.
- No strict container delivery requirement.

**Cons**:
- Conflicts with desired production operating model.
- Less reliable and less standardized operational posture.
- Higher drift risk between environments.

**Estimated Effort**: S

---

### Option 3: Split deployments into separate "in-cluster" and "remote-only" app variants

**Description**: Maintain two independently packaged runtimes with different auth and connection assumptions.

**Pros**:
- Could optimize each variant independently.

**Cons**:
- Doubles build/release/maintenance complexity.
- Harder operator UX and documentation burden.
- Divergent feature behavior risk.

**Estimated Effort**: L

## Decision

### Chosen Option

**We will implement Option 1: Kubernetes-primary runtime with hybrid authentication modes.**

### Rationale

Option 1 is the only option that fully satisfies Kubernetes-first deployment while preserving flexible connectivity to remote API endpoints. It keeps a single application path and minimizes architectural disruption while adding concrete deployment artifacts, image packaging, and auth documentation.

### Decision Matrix

| Criteria | Weight | Option 1 | Option 2 | Option 3 |
|----------|--------|----------|----------|----------|
| Kubernetes-first alignment | 5 | 5 | 2 | 4 |
| Auth flexibility | 5 | 5 | 4 | 3 |
| Operational simplicity | 4 | 4 | 3 | 2 |
| Delivery speed | 3 | 4 | 5 | 2 |
| Long-term maintainability | 4 | 4 | 2 | 1 |
| **Weighted Total** | | **86** | **65** | **52** |

## Consequences

### Positive

- Clear production deployment model centered on Kubernetes.
- In-cluster ServiceAccount path works as default behavior.
- Remote clusters remain accessible by mounted or pasted kubeconfig.
- One Docker image supports both Kubernetes and standalone execution.

### Negative

- Container image now includes `kubectl`, increasing image size.
- RBAC management becomes mandatory for each allowlisted namespace.
- Remote kubeconfig secret management adds operational responsibility.

### Risks

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Mis-scoped RBAC grants too much access | Med | High | Keep namespace-scoped runtime bindings and review policy regularly |
| Secret leakage for remote kubeconfig | Med | High | Use Kubernetes Secrets, limit reader access, rotate credentials |
| Backup copy regression in in-cluster mode | Low | High | Add tests for in-cluster kubeconfig generation and `kubectl cp` invocation |

## Technical Details

### Architecture

```text
┌───────────────────────────────────────┐
│ Kubernetes Deployment (primary)       │
│ - ServiceAccount: nkvm-runner         │
│ - App container + kubectl             │
│ - PVC: /var/lib/nkvm                  │
│ - Optional Secret: /etc/nkvm/remote   │
└───────────────────────────────────────┘
                    │
                    ├── In-cluster auth (default)
                    ├── Kubeconfig path auth
                    └── Pasted kubeconfig auth
                    │
                    ▼
        Kubernetes API (same or remote cluster)
```

### AWS Services Involved

| Service | Purpose | Configuration |
|---------|---------|---------------|
| N/A | N/A | Provider-agnostic Kubernetes deployment |

### API Changes

- No external API contract change (Streamlit UI app).

### Configuration

```yaml
NKVM_BACKUP_DIR: /var/lib/nkvm/backups
NKVM_METADATA_DB_PATH: /var/lib/nkvm/data/backups.db
NKVM_DEFAULT_AUTH_MODE: in-cluster
```

## Security Considerations

### Authentication & Authorization

- In-cluster path uses Pod ServiceAccount token.
- Remote path uses kubeconfig path or pasted kubeconfig.
- RBAC split remains: cluster discovery read-only + namespace-scoped runtime operations.

### Data Protection

- Backup artifacts and metadata are persisted on a dedicated PVC in Kubernetes deployments.
- Remote kubeconfigs should be mounted as Secrets and not committed in git.

## Implementation Plan

### Phase 1

- Add Dockerfile and `.dockerignore`.
- Add Kubernetes app manifests (`deploy/k8s/app/`).

### Phase 2

- Improve in-cluster copy behavior for `kubectl cp` by generating in-cluster kubeconfig.
- Keep kubeconfig/context behavior for remote clusters.

### Phase 3

- Update README, deployment docs, runbook, and auth examples.
- Update ADR index and link artifacts.

## Related Decisions

- `ADR-001` establishes MVP architecture and backup workflow.

## References

- `deploy/k8s/README.md`
- `deploy/k8s/app/`
- `deploy/k8s/rbac/`
- `docs/operations/authentication-methods.md`
