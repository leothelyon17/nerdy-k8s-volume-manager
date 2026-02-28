# Security Baseline: RBAC and Runtime Controls

This baseline defines the minimum security controls for running `nerdy-k8s-volume-manager` in production-like clusters.

Reference authentication examples: `docs/operations/authentication-methods.md`.
Reference release gate and traceability checklist: `docs/operations/adr-002-release-acceptance-checklist.md`.

## 1) RBAC Model

Use manifests in `deploy/k8s/rbac/`:

- `serviceaccount.yaml`
- `clusterrole-runtime.yaml`
- `rolebinding-runtime-template.yaml`
- `clusterrole-cluster-discovery.yaml`
- `clusterrolebinding-cluster-discovery.yaml`

### Recommended Permission Split

- **Namespace-scoped runtime access (required):** bind `nkvm-runtime` with a `RoleBinding` in each approved namespace only.
- **Cluster-wide discovery (read-only):** bind `nkvm-cluster-discovery` for in-cluster ServiceAccount deployments so cluster summary and all-namespace discovery can run.
  If your kubeconfig identity already has equivalent read access, this binding is not required.

### Required Verbs and Resources

- Discovery and owner resolution:
  - `namespaces`: `list`
  - `persistentvolumeclaims`: `list`
  - `pods`: `list`
  - `apps/replicasets`: `get`
  - `batch/jobs`: `get`
- Helper-pod backup execution:
  - `pods`: `create`, `get`, `delete`
  - `pods/exec`: `create`, `get`

## 2) Namespace Allowlist Operations

1. Maintain an explicit allowlist (for example `apps`, `data`, `platform`).
2. Create one `RoleBinding` per allowlisted namespace from `rolebinding-runtime-template.yaml`.
3. In the UI, use the namespace filter instead of blank/all-namespaces discovery when operating with strict namespace scope.
4. Treat namespace onboarding/offboarding as a change-controlled operation.

## 3) RBAC Validation Checklist (Pass/Fail)

Run this checklist after every namespace onboarding/offboarding change.

Set the identity and namespace targets:

```bash
SA_ID="system:serviceaccount:nerdy-k8s-volume-manager:nkvm-runner"
ALLOWED_NS="apps"
UNBOUND_NS="default"
```

### Runtime Access in Onboarded Namespace (Must Pass)

- [ ] PASS: `kubectl auth can-i --as="${SA_ID}" list persistentvolumeclaims -n "${ALLOWED_NS}"` returns `yes`.
- [ ] PASS: `kubectl auth can-i --as="${SA_ID}" list pods -n "${ALLOWED_NS}"` returns `yes`.
- [ ] PASS: `kubectl auth can-i --as="${SA_ID}" create pods -n "${ALLOWED_NS}"` returns `yes`.
- [ ] PASS: `kubectl auth can-i --as="${SA_ID}" create pods/exec -n "${ALLOWED_NS}"` returns `yes`.
- [ ] PASS: `kubectl auth can-i --as="${SA_ID}" delete pods -n "${ALLOWED_NS}"` returns `yes`.

### Runtime Access Outside Allowlist (Must Fail)

- [ ] PASS: `kubectl auth can-i --as="${SA_ID}" create pods -n "${UNBOUND_NS}"` returns `no`.
- [ ] PASS: `kubectl auth can-i --as="${SA_ID}" delete pods -n "${UNBOUND_NS}"` returns `no`.
- [ ] PASS: `kubectl auth can-i --as="${SA_ID}" create pods/exec -n "${UNBOUND_NS}"` returns `no`.

### Cluster Discovery Role Guardrails (Must Pass)

- [ ] PASS: `kubectl auth can-i --as="${SA_ID}" list namespaces` returns `yes`.
- [ ] PASS: `kubectl auth can-i --as="${SA_ID}" list persistentvolumeclaims --all-namespaces` returns `yes`.
- [ ] PASS: `kubectl auth can-i --as="${SA_ID}" list pods --all-namespaces` returns `yes`.
- [ ] PASS: `kubectl auth can-i --as="${SA_ID}" create namespaces` returns `no`.
- [ ] PASS: `kubectl auth can-i --as="${SA_ID}" update persistentvolumeclaims -n "${ALLOWED_NS}"` returns `no`.

Fail the release preflight if any check does not match expected output.

## 4) Kubeconfig Handling Baseline

- Prefer **in-cluster ServiceAccount auth** for deployed environments.
- Set `NKVM_DEFAULT_AUTH_MODE=in-cluster` for Kubernetes deployments.
- If file-based kubeconfig is used:
  - Keep it outside the repository.
  - Prefer Kubernetes Secret mounts for in-cluster remote-cluster access.
  - Enforce `0600` permissions on kubeconfig files.
  - Rotate and revoke credentials on role changes or incident response.
- If pasted kubeconfig is used for troubleshooting:
  - Restrict to short-lived operator sessions.
  - Clear temporary files after use.
  - Avoid sharing terminal/session history containing credentials.

Example:

```bash
chmod 600 ~/.kube/config
```

## 5) Host File-Permission Baseline

Backups and metadata may contain sensitive operational data.

- Create directories with owner-only access (`0700`).
- Create metadata database files with owner-only access (`0600`).
- Run the app as a dedicated non-root service account on the host/VM.

Example:

```bash
install -d -m 700 backups data
touch data/backups.db
chmod 600 data/backups.db
```

## 6) Release Acceptance and Rollback Pointers

Before any production cutover, execute and record the checks in:
- `docs/operations/adr-002-release-acceptance-checklist.md`

Immediate rollback/containment pointers:
- Stop new backup executions in the UI.
- Remove namespace runtime write access by deleting `nkvm-runtime` `RoleBinding` in affected namespaces.
- Keep or remove cluster discovery binding (`nkvm-cluster-discovery`) based on whether read-only inventory access is still required.
- Follow full rollback procedure in `docs/runbooks/mvp-operations.md` (Operational Rollback section).

## 7) Production Hardening Checklist

- [ ] `NKVM_HELPER_IMAGE` pinned by digest (not tag-only).
- [ ] Runtime ServiceAccount used; no broad admin kubeconfig in production.
- [ ] `nkvm-runtime` bound only to approved namespaces.
- [ ] `nkvm-cluster-discovery` bound (or equivalent read access provided by kubeconfig identity).
- [ ] Backup and metadata paths use restrictive filesystem permissions.
- [ ] Backup artifacts stored on encrypted disk and rotated per retention policy.
- [ ] Kubernetes audit logging enabled for pod create/exec/delete operations.
- [ ] Helper pod image sourced from trusted registry with vulnerability scanning.
