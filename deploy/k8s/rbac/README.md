# RBAC Manifests for Nerdy K8s Volume Manager

These manifests define least-privilege permissions for discovery and helper-pod backups.
They are designed to be used with the primary Kubernetes deployment in `deploy/k8s/app/`.

## Files

- `serviceaccount.yaml`: Runtime ServiceAccount for the app deployment.
- `clusterrole-runtime.yaml`: Namespaced runtime permissions (bind with a RoleBinding in each allowed namespace).
- `rolebinding-runtime-template.yaml`: Template binding for an allowlisted namespace.
- `clusterrole-cluster-discovery.yaml`: Cluster-wide read permissions for all-namespace discovery and cluster summary.
- `clusterrolebinding-cluster-discovery.yaml`: Binding for cluster-wide discovery role.

## Recommended Rollout

1. Apply namespace/app resources first:

   ```bash
   kubectl apply -k deploy/k8s/app
   ```

2. Apply shared ServiceAccount and ClusterRoles:

   ```bash
   kubectl apply -f deploy/k8s/rbac/serviceaccount.yaml
   kubectl apply -f deploy/k8s/rbac/clusterrole-runtime.yaml
   kubectl apply -f deploy/k8s/rbac/clusterrole-cluster-discovery.yaml
   ```

3. Onboard each allowlisted namespace with the workflow in `Namespace Onboarding Workflow`.

4. Apply cluster-wide discovery binding for in-cluster ServiceAccount deployments:

   ```bash
   kubectl apply -f deploy/k8s/rbac/clusterrolebinding-cluster-discovery.yaml
   ```

   If you run with a kubeconfig that already grants equivalent read access, this binding can be skipped.

## Namespace Onboarding Workflow

Use this workflow for every namespace that should allow helper pod backup operations.

1. Select the namespace and render a dedicated RoleBinding from the template:

   ```bash
   export NKVM_NAMESPACE=apps
   cp deploy/k8s/rbac/rolebinding-runtime-template.yaml /tmp/nkvm-runtime-${NKVM_NAMESPACE}.yaml
   sed -i "s/REPLACE_WITH_ALLOWED_NAMESPACE/${NKVM_NAMESPACE}/g" /tmp/nkvm-runtime-${NKVM_NAMESPACE}.yaml
   ```

2. Validate and apply the binding:

   ```bash
   kubectl apply --dry-run=server -f /tmp/nkvm-runtime-${NKVM_NAMESPACE}.yaml
   kubectl apply -f /tmp/nkvm-runtime-${NKVM_NAMESPACE}.yaml
   kubectl -n "${NKVM_NAMESPACE}" get rolebinding nkvm-runtime -o wide
   ```

3. Run RBAC preflight checks with ServiceAccount impersonation:

   ```bash
   SA_ID="system:serviceaccount:nerdy-k8s-volume-manager:nkvm-runner"
   kubectl auth can-i --as="${SA_ID}" list persistentvolumeclaims -n "${NKVM_NAMESPACE}"
   kubectl auth can-i --as="${SA_ID}" list pods -n "${NKVM_NAMESPACE}"
   kubectl auth can-i --as="${SA_ID}" create pods -n "${NKVM_NAMESPACE}"
   kubectl auth can-i --as="${SA_ID}" create pods/exec -n "${NKVM_NAMESPACE}"
   ```

   Expected result: each command returns `yes`.

4. Verify runtime permissions are denied in a namespace that is not onboarded:

   ```bash
   export NKVM_UNBOUND_NAMESPACE=default
   SA_ID="system:serviceaccount:nerdy-k8s-volume-manager:nkvm-runner"
   kubectl auth can-i --as="${SA_ID}" create pods -n "${NKVM_UNBOUND_NAMESPACE}"
   kubectl auth can-i --as="${SA_ID}" delete pods -n "${NKVM_UNBOUND_NAMESPACE}"
   ```

   Expected result: each command returns `no`.

5. Record the namespace onboarding change in your change-management system.

### Namespace Offboarding

Remove runtime access when a namespace leaves the allowlist:

```bash
kubectl -n <namespace> delete rolebinding nkvm-runtime
```

## Security Notes

- Keep runtime write permissions namespace-scoped via RoleBindings.
- Keep cluster-wide discovery read-only.
- Pair RBAC with namespace filtering and host/kubeconfig hardening from `docs/operations/security-baseline.md`.
- For remote-cluster access, mount kubeconfig through a Secret and use UI mode `Use kubeconfig path`.
