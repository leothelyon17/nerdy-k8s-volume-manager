from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import ftplib
import hashlib
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import time

from kubernetes import client
from kubernetes.client import ApiException
from kubernetes.stream import stream

from .metadata import BackupMetadataStore
from .models import BackupResult, VolumeRecord

SERVICEACCOUNT_TOKEN_PATH = Path("/var/run/secrets/kubernetes.io/serviceaccount/token")
SERVICEACCOUNT_CA_PATH = Path("/var/run/secrets/kubernetes.io/serviceaccount/ca.crt")


@dataclass(frozen=True)
class BackupManagerConfig:
    backup_dir: Path
    helper_image: str
    helper_pod_timeout_seconds: int
    kubeconfig_path: str | None
    context: str | None
    in_cluster_auth: bool = False
    helper_pod_startup_retries: int = 2
    destination_mode: str = "local"
    remote_destination: RemoteDestinationConfig | None = None


@dataclass(frozen=True)
class RemoteDestinationConfig:
    protocol: str
    host: str
    username: str
    password: str
    directory: str
    port: int | None = None


class BackupStageError(RuntimeError):
    def __init__(self, *, stage: str, reason: str) -> None:
        normalized_reason = reason.strip() or "unknown error"
        super().__init__(f"{stage} stage failed: {normalized_reason}")
        self.stage = stage


class BackupManager:
    def __init__(
        self,
        *,
        core_api: client.CoreV1Api,
        metadata_store: BackupMetadataStore,
        config: BackupManagerConfig,
    ) -> None:
        self.core_api = core_api
        self.metadata_store = metadata_store
        self.config = config
        self.config.backup_dir.mkdir(parents=True, exist_ok=True)

    def backup_many(self, volumes: list[VolumeRecord]) -> list[BackupResult]:
        results: list[BackupResult] = []
        for volume in volumes:
            result = self.backup_one(volume)
            self.metadata_store.record_result(result)
            results.append(result)
        return results

    def backup_one(self, volume: VolumeRecord) -> BackupResult:
        started_at = _utc_now_iso()
        pod_name = _helper_pod_name(volume.namespace, volume.pvc_name)
        archive_name = _archive_name(volume.namespace, volume.pvc_name)
        local_archive_path = self.config.backup_dir / archive_name
        remote_archive_path = f"/tmp/{archive_name}"
        status = "failed"
        backup_path: str | None = None
        checksum_sha256: str | None = None
        message = ""

        try:
            self._startup_helper_pod(volume=volume, pod_name=pod_name)
            self._execute_tar_archive(
                namespace=volume.namespace,
                pod_name=pod_name,
                remote_path=remote_archive_path,
            )
            self._copy_archive_from_helper_pod(
                namespace=volume.namespace,
                pod_name=pod_name,
                remote_path=remote_archive_path,
                local_path=local_archive_path,
            )
            checksum_sha256 = self._validate_archive_and_checksum(local_archive_path=local_archive_path)
            if self.config.destination_mode == "remote":
                backup_path = self._upload_archive_to_remote_destination(local_archive_path=local_archive_path)
                local_archive_path.unlink(missing_ok=True)
            else:
                backup_path = str(local_archive_path)
            status = "success"
        except BackupStageError as error:
            message = str(error)
        except Exception as error:  # pylint: disable=broad-except
            message = f"unexpected backup failure: {_error_message(error)}"
        finally:
            cleanup_failure = self._cleanup_helper_pod(namespace=volume.namespace, pod_name=pod_name)
            if cleanup_failure:
                message = f"{message}; {cleanup_failure}" if message else cleanup_failure
                if status == "success":
                    status = "failed"
                    backup_path = None
                    checksum_sha256 = None

        finished_at = _utc_now_iso()
        return BackupResult(
            namespace=volume.namespace,
            pvc_name=volume.pvc_name,
            pvc_uid=volume.pvc_uid,
            status=status,
            started_at=started_at,
            finished_at=finished_at,
            backup_path=backup_path,
            checksum_sha256=checksum_sha256,
            message=message,
        )

    def _startup_helper_pod(self, *, volume: VolumeRecord, pod_name: str) -> None:
        attempts = max(1, self.config.helper_pod_startup_retries + 1)
        for attempt in range(1, attempts + 1):
            try:
                self._create_helper_pod(volume=volume, pod_name=pod_name)
            except Exception as error:  # pylint: disable=broad-except
                if attempt < attempts and _is_retryable_startup_error(error):
                    self._best_effort_delete_helper_pod(namespace=volume.namespace, pod_name=pod_name)
                    time.sleep(1)
                    continue
                reason = _error_message(error)
                if attempts > 1 and _is_retryable_startup_error(error):
                    reason = f"{reason} (after {attempts} attempts)"
                raise BackupStageError(stage="create", reason=reason) from error

            try:
                self._wait_for_pod_running(namespace=volume.namespace, pod_name=pod_name)
                return
            except Exception as error:  # pylint: disable=broad-except
                if attempt < attempts and _is_retryable_startup_error(error):
                    self._best_effort_delete_helper_pod(namespace=volume.namespace, pod_name=pod_name)
                    time.sleep(1)
                    continue
                reason = _error_message(error)
                if attempts > 1 and _is_retryable_startup_error(error):
                    reason = f"{reason} (after {attempts} attempts)"
                raise BackupStageError(stage="wait", reason=reason) from error

        raise BackupStageError(stage="wait", reason="helper pod startup exhausted retries")

    def _execute_tar_archive(self, *, namespace: str, pod_name: str, remote_path: str) -> None:
        try:
            stream(
                self.core_api.connect_get_namespaced_pod_exec,
                pod_name,
                namespace,
                command=["sh", "-c", f"tar -czf {remote_path} -C /data ."],
                stderr=True,
                stdin=False,
                stdout=True,
                tty=False,
            )
        except Exception as error:  # pylint: disable=broad-except
            raise BackupStageError(stage="exec", reason=_error_message(error)) from error

    def _copy_archive_from_helper_pod(
        self,
        *,
        namespace: str,
        pod_name: str,
        remote_path: str,
        local_path: Path,
    ) -> None:
        try:
            self._kubectl_copy_from_pod(
                namespace=namespace,
                pod_name=pod_name,
                remote_path=remote_path,
                local_path=local_path,
            )
        except Exception as error:  # pylint: disable=broad-except
            raise BackupStageError(stage="copy", reason=_error_message(error)) from error

    def _validate_archive_and_checksum(self, *, local_archive_path: Path) -> str:
        try:
            if not local_archive_path.exists():
                raise RuntimeError(f"archive not found at {local_archive_path}")
            if local_archive_path.stat().st_size <= 0:
                raise RuntimeError(f"archive is empty at {local_archive_path}")
            return _sha256(local_archive_path)
        except Exception as error:  # pylint: disable=broad-except
            raise BackupStageError(stage="checksum", reason=_error_message(error)) from error

    def _upload_archive_to_remote_destination(self, *, local_archive_path: Path) -> str:
        try:
            remote_destination = self.config.remote_destination
            if remote_destination is None:
                raise RuntimeError("remote destination configuration is missing")

            protocol = remote_destination.protocol.strip().lower()
            if protocol in {"ftp", "ftps"}:
                return self._upload_archive_with_ftp(
                    local_archive_path=local_archive_path,
                    remote_destination=remote_destination,
                    protocol=protocol,
                )
            if protocol == "scp":
                return self._upload_archive_with_scp(
                    local_archive_path=local_archive_path,
                    remote_destination=remote_destination,
                )
            if protocol == "rsync":
                return self._upload_archive_with_rsync(
                    local_archive_path=local_archive_path,
                    remote_destination=remote_destination,
                )

            raise RuntimeError(f"unsupported remote protocol: {remote_destination.protocol}")
        except Exception as error:  # pylint: disable=broad-except
            raise BackupStageError(stage="remote", reason=_error_message(error)) from error

    def _upload_archive_with_ftp(
        self,
        *,
        local_archive_path: Path,
        remote_destination: RemoteDestinationConfig,
        protocol: str,
    ) -> str:
        remote_directory = _normalize_remote_directory(remote_destination.directory)
        ftp_class: type[ftplib.FTP] = ftplib.FTP_TLS if protocol == "ftps" else ftplib.FTP
        ftp_port = remote_destination.port or 21
        archive_name = local_archive_path.name
        with ftp_class(timeout=60) as ftp_client:
            ftp_client.connect(host=remote_destination.host, port=ftp_port)
            ftp_client.login(user=remote_destination.username, passwd=remote_destination.password)
            if protocol == "ftps" and isinstance(ftp_client, ftplib.FTP_TLS):
                ftp_client.prot_p()
            self._ensure_ftp_directory(ftp_client=ftp_client, directory=remote_directory)
            with local_archive_path.open("rb") as file_handle:
                ftp_client.storbinary(f"STOR {archive_name}", file_handle)

        return _remote_artifact_reference(
            protocol=protocol,
            host=remote_destination.host,
            directory=remote_directory,
            archive_name=archive_name,
        )

    def _upload_archive_with_scp(
        self,
        *,
        local_archive_path: Path,
        remote_destination: RemoteDestinationConfig,
    ) -> str:
        remote_directory = _normalize_remote_directory(remote_destination.directory)
        archive_name = local_archive_path.name
        remote_target = (
            f"{remote_destination.username}@{remote_destination.host}:{remote_directory}/{archive_name}"
        )
        scp_binary = shutil.which("scp")
        if scp_binary is None:
            raise RuntimeError("scp is required for SCP remote uploads but was not found in PATH")

        self._ensure_remote_directory_over_ssh(
            remote_destination=remote_destination,
            remote_directory=remote_directory,
        )
        self._run_sshpass_command(
            command=[
                scp_binary,
                "-o",
                "StrictHostKeyChecking=no",
                "-o",
                "UserKnownHostsFile=/dev/null",
                str(local_archive_path),
                remote_target,
            ],
            password=remote_destination.password,
        )
        return _remote_artifact_reference(
            protocol="scp",
            host=remote_destination.host,
            directory=remote_directory,
            archive_name=archive_name,
        )

    def _upload_archive_with_rsync(
        self,
        *,
        local_archive_path: Path,
        remote_destination: RemoteDestinationConfig,
    ) -> str:
        remote_directory = _normalize_remote_directory(remote_destination.directory)
        archive_name = local_archive_path.name
        remote_target = (
            f"{remote_destination.username}@{remote_destination.host}:{remote_directory}/{archive_name}"
        )
        rsync_binary = shutil.which("rsync")
        if rsync_binary is None:
            raise RuntimeError("rsync is required for RSYNC remote uploads but was not found in PATH")

        self._ensure_remote_directory_over_ssh(
            remote_destination=remote_destination,
            remote_directory=remote_directory,
        )
        self._run_sshpass_command(
            command=[
                rsync_binary,
                "-az",
                "-e",
                "ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null",
                str(local_archive_path),
                remote_target,
            ],
            password=remote_destination.password,
        )
        return _remote_artifact_reference(
            protocol="rsync",
            host=remote_destination.host,
            directory=remote_directory,
            archive_name=archive_name,
        )

    def _ensure_remote_directory_over_ssh(
        self,
        *,
        remote_destination: RemoteDestinationConfig,
        remote_directory: str,
    ) -> None:
        ssh_binary = shutil.which("ssh")
        if ssh_binary is None:
            raise RuntimeError("ssh is required for SCP/RSYNC remote uploads but was not found in PATH")

        remote_host = f"{remote_destination.username}@{remote_destination.host}"
        remote_command = f"mkdir -p {shlex.quote(remote_directory)}"
        self._run_sshpass_command(
            command=[
                ssh_binary,
                "-o",
                "StrictHostKeyChecking=no",
                "-o",
                "UserKnownHostsFile=/dev/null",
                remote_host,
                remote_command,
            ],
            password=remote_destination.password,
        )

    def _run_sshpass_command(self, *, command: list[str], password: str) -> None:
        sshpass_binary = shutil.which("sshpass")
        if sshpass_binary is None:
            raise RuntimeError("sshpass is required for password-based SCP/RSYNC uploads but was not found in PATH")

        environment = os.environ.copy()
        environment["SSHPASS"] = password
        completed = subprocess.run(
            [sshpass_binary, "-e", *command],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or "remote transfer command failed")

    def _ensure_ftp_directory(self, *, ftp_client: ftplib.FTP, directory: str) -> None:
        if directory == "/":
            ftp_client.cwd("/")
            return

        ftp_client.cwd("/")
        path_components = [component for component in directory.split("/") if component]
        for component in path_components:
            try:
                ftp_client.cwd(component)
            except ftplib.error_perm:
                ftp_client.mkd(component)
                ftp_client.cwd(component)

    def _best_effort_delete_helper_pod(self, *, namespace: str, pod_name: str) -> None:
        try:
            self._delete_helper_pod(namespace=namespace, pod_name=pod_name)
        except Exception:  # pylint: disable=broad-except
            return

    def _cleanup_helper_pod(self, *, namespace: str, pod_name: str) -> str | None:
        try:
            self._delete_helper_pod(namespace=namespace, pod_name=pod_name)
            return None
        except Exception as error:  # pylint: disable=broad-except
            return f"cleanup stage failed: {_error_message(error)}"

    def _create_helper_pod(self, *, volume: VolumeRecord, pod_name: str) -> None:
        preferred_node_name = self._find_running_pvc_consumer_node(
            namespace=volume.namespace,
            pvc_name=volume.pvc_name,
        )
        pod = client.V1Pod(
            metadata=client.V1ObjectMeta(
                name=pod_name,
                labels={
                    "app.kubernetes.io/name": "nerdy-k8s-volume-manager",
                    "app.kubernetes.io/component": "backup-helper",
                },
            ),
            spec=client.V1PodSpec(
                restart_policy="Never",
                node_name=preferred_node_name,
                containers=[
                    client.V1Container(
                        name="backup-helper",
                        image=self.config.helper_image,
                        command=["sh", "-c", "sleep 3600"],
                        volume_mounts=[
                            # Read-only mount protects workloads from accidental writes.
                            client.V1VolumeMount(name="target", mount_path="/data", read_only=True)
                        ],
                    )
                ],
                volumes=[
                    client.V1Volume(
                        name="target",
                        persistent_volume_claim=client.V1PersistentVolumeClaimVolumeSource(
                            claim_name=volume.pvc_name,
                            read_only=True,
                        ),
                    )
                ],
            ),
        )

        try:
            self.core_api.create_namespaced_pod(namespace=volume.namespace, body=pod)
        except ApiException as error:
            if error.status != 409:
                raise
            self._delete_helper_pod(namespace=volume.namespace, pod_name=pod_name)
            self.core_api.create_namespaced_pod(namespace=volume.namespace, body=pod)

    def _find_running_pvc_consumer_node(self, *, namespace: str, pvc_name: str) -> str | None:
        try:
            response = self.core_api.list_namespaced_pod(namespace=namespace)
        except Exception:  # pylint: disable=broad-except
            return None

        pod_items = getattr(response, "items", None)
        if not isinstance(pod_items, list):
            return None

        for pod in pod_items:
            pod_spec = getattr(pod, "spec", None)
            pod_status = getattr(pod, "status", None)
            if pod_spec is None or pod_status is None:
                continue

            if getattr(pod_status, "phase", None) != "Running":
                continue

            node_name = getattr(pod_spec, "node_name", None)
            if not node_name:
                continue

            volumes = getattr(pod_spec, "volumes", None) or []
            for declared_volume in volumes:
                pvc_reference = getattr(declared_volume, "persistent_volume_claim", None)
                if pvc_reference and getattr(pvc_reference, "claim_name", None) == pvc_name:
                    return node_name

        return None

    def _wait_for_pod_running(self, *, namespace: str, pod_name: str) -> None:
        deadline = time.time() + self.config.helper_pod_timeout_seconds
        last_phase = "Unknown"
        last_hint: str | None = None
        while time.time() < deadline:
            pod = self.core_api.read_namespaced_pod(namespace=namespace, name=pod_name)
            phase = pod.status.phase if pod.status and pod.status.phase else "Unknown"
            last_phase = phase
            pending_hint = _extract_pending_hint(pod)
            if pending_hint:
                last_hint = pending_hint
            if phase == "Running":
                return
            if phase in {"Failed", "Succeeded"}:
                raise RuntimeError(f"helper pod entered unexpected phase: {phase}")
            time.sleep(2)

        detail = f"last observed phase={last_phase}"
        if last_hint:
            detail = f"{detail}; {last_hint}"
        raise TimeoutError(f"helper pod {namespace}/{pod_name} did not become Running in time ({detail})")

    def _delete_helper_pod(self, *, namespace: str, pod_name: str) -> None:
        try:
            self.core_api.delete_namespaced_pod(
                name=pod_name,
                namespace=namespace,
                grace_period_seconds=0,
                body=client.V1DeleteOptions(),
            )
        except ApiException as error:
            if error.status in {403, 404}:
                return
            raise

    def _kubectl_copy_from_pod(
        self,
        *,
        namespace: str,
        pod_name: str,
        remote_path: str,
        local_path: Path,
    ) -> None:
        kubectl = shutil.which("kubectl")
        if kubectl is None:
            raise RuntimeError("kubectl is required for backup copy but was not found in PATH")

        command = [kubectl]
        generated_kubeconfig_path: Path | None = None
        try:
            kubeconfig_path = self.config.kubeconfig_path.strip() if self.config.kubeconfig_path else None
            skip_context = False

            if self.config.in_cluster_auth and not kubeconfig_path:
                generated_kubeconfig_path = _build_incluster_kubeconfig()
                kubeconfig_path = str(generated_kubeconfig_path)
                skip_context = True

            if kubeconfig_path:
                resolved_kubeconfig_path = str(Path(kubeconfig_path).expanduser())
                kubeconfig_file = Path(resolved_kubeconfig_path)
                if not kubeconfig_file.exists():
                    raise RuntimeError(f"kubeconfig path does not exist: {resolved_kubeconfig_path}")
                if not kubeconfig_file.is_file():
                    raise RuntimeError(f"kubeconfig path is not a file: {resolved_kubeconfig_path}")
                if not os.access(kubeconfig_file, os.R_OK):
                    raise RuntimeError(f"kubeconfig path is not readable: {resolved_kubeconfig_path}")
                command.extend(["--kubeconfig", resolved_kubeconfig_path])
            if self.config.context and not skip_context:
                command.extend(["--context", self.config.context])

            command.extend(["-n", namespace, "cp", f"{pod_name}:{remote_path}", str(local_path)])
            completed = subprocess.run(command, check=False, capture_output=True, text=True)
            if completed.returncode != 0:
                raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or "kubectl cp failed")
        finally:
            if generated_kubeconfig_path is not None:
                generated_kubeconfig_path.unlink(missing_ok=True)


def _build_incluster_kubeconfig() -> Path:
    _validate_service_account_file(
        path=SERVICEACCOUNT_TOKEN_PATH,
        description="service account token",
    )
    _validate_service_account_file(
        path=SERVICEACCOUNT_CA_PATH,
        description="service account CA bundle",
    )

    api_host = os.getenv("KUBERNETES_SERVICE_HOST", "").strip()
    if not api_host:
        raise RuntimeError("KUBERNETES_SERVICE_HOST is required for in-cluster authentication")
    api_port = (
        os.getenv("KUBERNETES_SERVICE_PORT_HTTPS", "").strip()
        or os.getenv("KUBERNETES_SERVICE_PORT", "").strip()
        or "443"
    )
    api_server = f"https://{api_host}:{api_port}"

    kubeconfig_content = f"""apiVersion: v1
kind: Config
clusters:
- name: in-cluster
  cluster:
    certificate-authority: {SERVICEACCOUNT_CA_PATH}
    server: {api_server}
contexts:
- name: in-cluster
  context:
    cluster: in-cluster
    user: service-account
current-context: in-cluster
users:
- name: service-account
  user:
    tokenFile: {SERVICEACCOUNT_TOKEN_PATH}
"""

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as handle:
        handle.write(kubeconfig_content)
        kubeconfig_path = Path(handle.name)

    os.chmod(kubeconfig_path, 0o600)
    return kubeconfig_path


def _utc_now_iso() -> str:
    return datetime.now(tz=UTC).replace(microsecond=0).isoformat()


def _helper_pod_name(namespace: str, pvc_name: str) -> str:
    timestamp = datetime.now(tz=UTC).strftime("%Y%m%d%H%M%S")
    base = f"nkv-backup-{namespace}-{pvc_name}-{timestamp}"
    return _sanitize_dns_label(base, max_length=63)


def _archive_name(namespace: str, pvc_name: str) -> str:
    timestamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    safe_namespace = _sanitize_filesystem_component(namespace)
    safe_pvc_name = _sanitize_filesystem_component(pvc_name)
    return f"{timestamp}__{safe_namespace}__{safe_pvc_name}.tar.gz"


def _sanitize_dns_label(value: str, max_length: int) -> str:
    lowered = value.lower()
    normalized = re.sub(r"[^a-z0-9-]", "-", lowered).strip("-")
    normalized = re.sub(r"-+", "-", normalized)
    if len(normalized) > max_length:
        normalized = normalized[:max_length].rstrip("-")
    return normalized or "nkv-backup"


def _sanitize_filesystem_component(value: str) -> str:
    sanitized = re.sub(r"[^a-zA-Z0-9._-]", "_", value)
    return sanitized or "unknown"


def _normalize_remote_directory(value: str) -> str:
    normalized = re.sub(r"/+", "/", value.strip())
    if not normalized:
        return "/"
    if not normalized.startswith("/"):
        normalized = f"/{normalized}"
    if len(normalized) > 1:
        normalized = normalized.rstrip("/")
    return normalized or "/"


def _remote_artifact_reference(*, protocol: str, host: str, directory: str, archive_name: str) -> str:
    directory_segment = "" if directory == "/" else directory
    return f"{protocol}://{host}{directory_segment}/{archive_name}"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_handle:
        for chunk in iter(lambda: file_handle.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_retryable_startup_error(error: Exception) -> bool:
    if isinstance(error, TimeoutError):
        return True
    if isinstance(error, ApiException):
        if error.status in {400, 401, 403, 404, 422}:
            return False
        return True
    return isinstance(error, RuntimeError)


def _error_message(error: Exception) -> str:
    message = str(error).strip()
    return message or error.__class__.__name__


def _validate_service_account_file(*, path: Path, description: str) -> None:
    if not path.exists():
        raise RuntimeError(f"in-cluster authentication requires a {description} at {path}")
    if not path.is_file():
        raise RuntimeError(f"in-cluster authentication requires {description} file at {path}")
    if not os.access(path, os.R_OK):
        raise RuntimeError(f"in-cluster authentication requires readable {description} at {path}")


def _extract_pending_hint(pod: object) -> str | None:
    pod_status = getattr(pod, "status", None)
    if pod_status is None:
        return None

    conditions = getattr(pod_status, "conditions", None) or []
    for condition in conditions:
        if getattr(condition, "type", None) == "PodScheduled" and getattr(condition, "status", None) == "False":
            reason = getattr(condition, "reason", None) or "Unschedulable"
            message = (getattr(condition, "message", None) or "").strip()
            return f"pod unschedulable ({reason}: {message})" if message else f"pod unschedulable ({reason})"

    for attribute in ("init_container_statuses", "container_statuses"):
        container_statuses = getattr(pod_status, attribute, None) or []
        for container_status in container_statuses:
            state = getattr(container_status, "state", None)
            waiting_state = getattr(state, "waiting", None) if state is not None else None
            if waiting_state is None:
                continue
            reason = getattr(waiting_state, "reason", None) or "ContainerWaiting"
            message = (getattr(waiting_state, "message", None) or "").strip()
            if message:
                return f"container waiting ({reason}: {message})"
            return f"container waiting ({reason})"

    return None
