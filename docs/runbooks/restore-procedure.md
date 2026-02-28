# Restore Procedure (MVP)

This procedure restores a PVC from an existing `.tar.gz` backup artifact created by `nerdy-k8s-volume-manager`.

The MVP restore flow is operator-driven and intentionally explicit:

1. validate the selected backup artifact
2. quiesce the target workload
3. copy/extract archive into the target PVC via a short-lived restore pod
4. verify restored data before resuming traffic

## Preconditions

- `kubectl` is installed and points to the correct cluster/context.
- You have write access to the target namespace and PVC.
- You have a known-good backup artifact path from:
  - Streamlit `Recent Backup History`, or
  - metadata DB (`backup_history` table).
- You understand application-level restore semantics (overwrite vs merge).

## Required Variables

Set these before starting:

```bash
export TARGET_NAMESPACE="<namespace>"
export TARGET_PVC="<pvc-name>"
export ARCHIVE_PATH="<absolute-or-relative-path-to-archive.tar.gz>"
export EXPECTED_SHA256="<checksum-from-backup-history>"
export RESTORE_POD="nkvm-restore-$(date -u +%Y%m%d%H%M%S)"
export NKVM_HELPER_IMAGE="${NKVM_HELPER_IMAGE:-alpine:3.20}"
```

## Step 1: Identify and Validate Backup Candidate

1. Confirm backup metadata record exists:

```bash
sqlite3 "${NKVM_METADATA_DB_PATH:-./data/backups.db}" \
  "SELECT namespace,pvc_name,status,created_at,backup_path,checksum_sha256 FROM backup_history WHERE status='success' ORDER BY created_at DESC, id DESC LIMIT 20;"
```

2. Verify archive file exists and is non-empty:

```bash
test -s "${ARCHIVE_PATH}"
```

3. Verify checksum:

```bash
ACTUAL_SHA256="$(sha256sum "${ARCHIVE_PATH}" | awk '{print $1}')"
test "${ACTUAL_SHA256}" = "${EXPECTED_SHA256}"
```

4. Optional quick archive integrity/listing check:

```bash
tar -tzf "${ARCHIVE_PATH}" | head -n 40
```

## Step 2: Quiesce Target Workload

Scale down writers before restore. Pick the workload type you run:

```bash
kubectl -n "${TARGET_NAMESPACE}" scale deployment/<name> --replicas=0
```

```bash
kubectl -n "${TARGET_NAMESPACE}" scale statefulset/<name> --replicas=0
```

Wait until running writers are fully stopped before proceeding.

## Step 3: Create Restore Pod with PVC Mount

```bash
cat <<EOF >/tmp/nkvm-restore-pod.yaml
apiVersion: v1
kind: Pod
metadata:
  name: ${RESTORE_POD}
  labels:
    app.kubernetes.io/name: nerdy-k8s-volume-manager
    app.kubernetes.io/component: restore-helper
spec:
  restartPolicy: Never
  containers:
    - name: restore-helper
      image: ${NKVM_HELPER_IMAGE}
      command: ["sh", "-c", "sleep 3600"]
      volumeMounts:
        - name: target
          mountPath: /restore
  volumes:
    - name: target
      persistentVolumeClaim:
        claimName: ${TARGET_PVC}
EOF

kubectl -n "${TARGET_NAMESPACE}" apply -f /tmp/nkvm-restore-pod.yaml
kubectl -n "${TARGET_NAMESPACE}" wait --for=condition=Ready "pod/${RESTORE_POD}" --timeout=180s
```

## Step 4: Copy and Extract Archive

1. Copy archive into restore pod:

```bash
kubectl -n "${TARGET_NAMESPACE}" cp "${ARCHIVE_PATH}" "${RESTORE_POD}:/tmp/restore.tar.gz"
```

2. Extract into mounted PVC:

```bash
kubectl -n "${TARGET_NAMESPACE}" exec "${RESTORE_POD}" -- sh -c "tar -xzf /tmp/restore.tar.gz -C /restore"
```

If your application requires a clean target directory, perform an explicit wipe only after approval and snapshot/backup confirmation.

## Step 5: Verify Restored Data

1. Verify files are present in mounted PVC:

```bash
kubectl -n "${TARGET_NAMESPACE}" exec "${RESTORE_POD}" -- sh -c "find /restore -maxdepth 3 -type f | head -n 40"
kubectl -n "${TARGET_NAMESPACE}" exec "${RESTORE_POD}" -- sh -c "du -sh /restore"
```

2. Run app-specific validation checks (schema/version markers, expected files, startup probes).

3. Resume workload:

```bash
kubectl -n "${TARGET_NAMESPACE}" scale deployment/<name> --replicas=1
```

```bash
kubectl -n "${TARGET_NAMESPACE}" scale statefulset/<name> --replicas=1
```

4. Verify workload readiness:

```bash
kubectl -n "${TARGET_NAMESPACE}" get pods
kubectl -n "${TARGET_NAMESPACE}" rollout status deployment/<name> --timeout=300s
```

```bash
kubectl -n "${TARGET_NAMESPACE}" rollout status statefulset/<name> --timeout=300s
```

## Step 6: Cleanup

```bash
kubectl -n "${TARGET_NAMESPACE}" delete pod "${RESTORE_POD}" --ignore-not-found
rm -f /tmp/nkvm-restore-pod.yaml
```

## Failure Handling Matrix

| Step | Failure Signal | Action |
|---|---|---|
| 1 (validation) | checksum mismatch or unreadable archive | Stop restore. Select another successful artifact from `backup_history`. |
| 2 (quiesce) | pods keep restarting/writing | Fix controller automation or PDB constraints, then retry quiesce. |
| 3 (restore pod create/wait) | pod does not become Ready | `kubectl describe pod`, inspect events/image pull, verify RBAC and PVC binding. |
| 4 (copy) | `kubectl cp` fails | Verify local file path, pod name, namespace, and local disk health. |
| 4 (extract) | tar extraction error | Re-validate archive with `tar -tzf`, confirm target volume free space and filesystem permissions. |
| 5 (post-restore app checks) | app fails readiness/health checks | Keep workload scaled down, collect logs, retry restore from prior known-good artifact. |

## Success Criteria

- Target workload restarts cleanly after restore.
- Application-level validation passes.
- Incident/change record includes:
  - restored PVC and namespace
  - artifact path and checksum
  - operator, timestamp, and verification outcome
