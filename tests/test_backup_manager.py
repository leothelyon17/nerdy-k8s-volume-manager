from __future__ import annotations

import ftplib
import hashlib
from pathlib import Path
import subprocess
from types import SimpleNamespace
from unittest.mock import ANY, Mock

import pytest
from kubernetes.client import ApiException

from nerdy_k8s_volume_manager.backup import (
    BackupManager,
    BackupManagerConfig,
    RemoteDestinationConfig,
    BackupStageError,
    _build_incluster_kubeconfig,
    _error_message,
    _is_retryable_startup_error,
)
from nerdy_k8s_volume_manager.models import BackupResult, VolumeRecord


def _volume_record() -> VolumeRecord:
    return VolumeRecord(
        namespace="apps",
        pvc_name="db-data",
        pvc_uid="pvc-uid-123",
        phase="Bound",
        capacity="1Gi",
        storage_class="fast",
        access_modes=("ReadWriteOnce",),
        bound_pv="pv-db-data",
        app_kind="StatefulSet",
        app_name="postgres",
        last_successful_backup_at=None,
    )


def _backup_manager(tmp_path: Path, *, startup_retries: int = 0) -> BackupManager:
    return BackupManager(
        core_api=Mock(),
        metadata_store=Mock(),
        config=BackupManagerConfig(
            backup_dir=tmp_path,
            helper_image="alpine:3.20",
            helper_pod_timeout_seconds=15,
            kubeconfig_path=None,
            context=None,
            helper_pod_startup_retries=startup_retries,
        ),
    )


def test_backup_one_with_wait_timeout_returns_wait_stage_failure_and_cleans_up(tmp_path: Path) -> None:
    manager = _backup_manager(tmp_path, startup_retries=0)
    volume = _volume_record()

    manager._create_helper_pod = Mock()  # type: ignore[method-assign]
    manager._wait_for_pod_running = Mock(side_effect=TimeoutError("pod startup timed out"))  # type: ignore[method-assign]
    manager._delete_helper_pod = Mock()  # type: ignore[method-assign]

    result = manager.backup_one(volume)

    assert result.status == "failed"
    assert "wait stage failed" in result.message
    assert "pod startup timed out" in result.message
    manager._delete_helper_pod.assert_called_once_with(namespace="apps", pod_name=ANY)


def test_backup_one_with_transient_wait_timeout_retries_before_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _backup_manager(tmp_path, startup_retries=1)
    volume = _volume_record()

    manager._create_helper_pod = Mock()  # type: ignore[method-assign]
    manager._wait_for_pod_running = Mock(side_effect=[TimeoutError("startup timeout"), None])  # type: ignore[method-assign]
    manager._execute_tar_archive = Mock()  # type: ignore[method-assign]
    manager._copy_archive_from_helper_pod = Mock()  # type: ignore[method-assign]
    manager._validate_archive_and_checksum = Mock(return_value="abc123")  # type: ignore[method-assign]
    manager._delete_helper_pod = Mock()  # type: ignore[method-assign]

    monkeypatch.setattr("nerdy_k8s_volume_manager.backup.time.sleep", lambda _: None)

    result = manager.backup_one(volume)

    assert result.status == "success"
    assert manager._create_helper_pod.call_count == 2
    assert manager._wait_for_pod_running.call_count == 2
    assert manager._delete_helper_pod.call_count == 2


def test_backup_one_with_missing_kubectl_returns_copy_stage_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _backup_manager(tmp_path)
    volume = _volume_record()

    manager._startup_helper_pod = Mock()  # type: ignore[method-assign]
    manager._execute_tar_archive = Mock()  # type: ignore[method-assign]
    manager._delete_helper_pod = Mock()  # type: ignore[method-assign]

    monkeypatch.setattr("nerdy_k8s_volume_manager.backup.shutil.which", lambda _: None)

    result = manager.backup_one(volume)

    assert result.status == "failed"
    assert "copy stage failed" in result.message
    assert "kubectl is required" in result.message
    manager._delete_helper_pod.assert_called_once_with(namespace="apps", pod_name=ANY)


def test_backup_one_with_nonzero_kubectl_cp_exit_returns_copy_stage_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _backup_manager(tmp_path)
    volume = _volume_record()

    manager._startup_helper_pod = Mock()  # type: ignore[method-assign]
    manager._execute_tar_archive = Mock()  # type: ignore[method-assign]
    manager._delete_helper_pod = Mock()  # type: ignore[method-assign]

    monkeypatch.setattr("nerdy_k8s_volume_manager.backup.shutil.which", lambda _: "/usr/bin/kubectl")
    monkeypatch.setattr(
        "nerdy_k8s_volume_manager.backup.subprocess.run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=["kubectl", "cp"],
            returncode=1,
            stdout="",
            stderr="copy failed from pod",
        ),
    )

    result = manager.backup_one(volume)

    assert result.status == "failed"
    assert "copy stage failed" in result.message
    assert "copy failed from pod" in result.message
    manager._delete_helper_pod.assert_called_once_with(namespace="apps", pod_name=ANY)


def test_backup_one_with_successful_copy_returns_checksum_and_runs_cleanup(tmp_path: Path) -> None:
    manager = _backup_manager(tmp_path)
    volume = _volume_record()

    manager._startup_helper_pod = Mock()  # type: ignore[method-assign]
    manager._execute_tar_archive = Mock()  # type: ignore[method-assign]
    manager._delete_helper_pod = Mock()  # type: ignore[method-assign]

    archive_bytes = b"backup-archive-payload"

    def _fake_copy(*, namespace: str, pod_name: str, remote_path: str, local_path: Path) -> None:
        assert namespace == volume.namespace
        assert pod_name
        assert remote_path.startswith("/tmp/")
        local_path.write_bytes(archive_bytes)

    manager._copy_archive_from_helper_pod = Mock(side_effect=_fake_copy)  # type: ignore[method-assign]

    result = manager.backup_one(volume)

    assert result.status == "success"
    assert result.backup_path is not None
    assert result.message == ""
    assert result.checksum_sha256 == hashlib.sha256(archive_bytes).hexdigest()
    manager._delete_helper_pod.assert_called_once_with(namespace="apps", pod_name=ANY)


def test_backup_one_with_remote_destination_uploads_and_returns_remote_backup_path(tmp_path: Path) -> None:
    manager = BackupManager(
        core_api=Mock(),
        metadata_store=Mock(),
        config=BackupManagerConfig(
            backup_dir=tmp_path,
            helper_image="alpine:3.20",
            helper_pod_timeout_seconds=15,
            kubeconfig_path=None,
            context=None,
            destination_mode="remote",
            remote_destination=RemoteDestinationConfig(
                protocol="ftp",
                host="backup.internal",
                username="svc-backup",
                password="secret",
                directory="/archives/daily",
            ),
        ),
    )
    volume = _volume_record()

    manager._startup_helper_pod = Mock()  # type: ignore[method-assign]
    manager._execute_tar_archive = Mock()  # type: ignore[method-assign]
    manager._delete_helper_pod = Mock()  # type: ignore[method-assign]
    manager._copy_archive_from_helper_pod = Mock(  # type: ignore[method-assign]
        side_effect=lambda *, namespace, pod_name, remote_path, local_path: local_path.write_bytes(b"remote-ok")
    )
    manager._upload_archive_to_remote_destination = Mock(  # type: ignore[method-assign]
        return_value="ftp://backup.internal/archives/daily/archive.tar.gz"
    )

    result = manager.backup_one(volume)

    assert result.status == "success"
    assert result.backup_path == "ftp://backup.internal/archives/daily/archive.tar.gz"
    assert list(tmp_path.glob("*.tar.gz")) == []
    manager._delete_helper_pod.assert_called_once_with(namespace="apps", pod_name=ANY)


def test_backup_one_with_remote_destination_upload_failure_returns_remote_stage_error(tmp_path: Path) -> None:
    manager = BackupManager(
        core_api=Mock(),
        metadata_store=Mock(),
        config=BackupManagerConfig(
            backup_dir=tmp_path,
            helper_image="alpine:3.20",
            helper_pod_timeout_seconds=15,
            kubeconfig_path=None,
            context=None,
            destination_mode="remote",
            remote_destination=RemoteDestinationConfig(
                protocol="ftp",
                host="backup.internal",
                username="svc-backup",
                password="secret",
                directory="/archives/daily",
            ),
        ),
    )
    volume = _volume_record()

    manager._startup_helper_pod = Mock()  # type: ignore[method-assign]
    manager._execute_tar_archive = Mock()  # type: ignore[method-assign]
    manager._delete_helper_pod = Mock()  # type: ignore[method-assign]
    manager._copy_archive_from_helper_pod = Mock(  # type: ignore[method-assign]
        side_effect=lambda *, namespace, pod_name, remote_path, local_path: local_path.write_bytes(b"remote-ok")
    )
    manager._upload_archive_to_remote_destination = Mock(  # type: ignore[method-assign]
        side_effect=BackupStageError(stage="remote", reason="authentication failed")
    )

    result = manager.backup_one(volume)

    assert result.status == "failed"
    assert "remote stage failed: authentication failed" in result.message
    assert len(list(tmp_path.glob("*.tar.gz"))) == 1
    manager._delete_helper_pod.assert_called_once_with(namespace="apps", pod_name=ANY)


def test_backup_many_records_all_results_in_metadata_store(tmp_path: Path) -> None:
    manager = _backup_manager(tmp_path)
    volume_a = _volume_record()
    volume_b = VolumeRecord(
        namespace="apps",
        pvc_name="cache-data",
        pvc_uid="pvc-uid-456",
        phase="Bound",
        capacity="2Gi",
        storage_class="fast",
        access_modes=("ReadWriteOnce",),
        bound_pv="pv-cache-data",
        app_kind="Deployment",
        app_name="api",
        last_successful_backup_at=None,
    )
    result_a = BackupResult(
        namespace="apps",
        pvc_name="db-data",
        pvc_uid="pvc-uid-123",
        status="success",
        started_at="2026-02-23T10:00:00+00:00",
        finished_at="2026-02-23T10:01:00+00:00",
        backup_path="/tmp/a.tar.gz",
        checksum_sha256="aaa",
        message="",
    )
    result_b = BackupResult(
        namespace="apps",
        pvc_name="cache-data",
        pvc_uid="pvc-uid-456",
        status="failed",
        started_at="2026-02-23T10:02:00+00:00",
        finished_at="2026-02-23T10:03:00+00:00",
        backup_path=None,
        checksum_sha256=None,
        message="copy stage failed: simulated",
    )

    manager.backup_one = Mock(side_effect=[result_a, result_b])  # type: ignore[method-assign]

    results = manager.backup_many([volume_a, volume_b])

    assert results == [result_a, result_b]
    assert manager.metadata_store.record_result.call_count == 2


def test_backup_one_with_unexpected_error_returns_unexpected_failure_message(tmp_path: Path) -> None:
    manager = _backup_manager(tmp_path)
    volume = _volume_record()

    manager._startup_helper_pod = Mock(side_effect=ValueError("unexpected blowup"))  # type: ignore[method-assign]
    manager._delete_helper_pod = Mock()  # type: ignore[method-assign]

    result = manager.backup_one(volume)

    assert result.status == "failed"
    assert "unexpected backup failure: unexpected blowup" in result.message
    manager._delete_helper_pod.assert_called_once()


def test_backup_one_with_cleanup_failure_marks_successful_backup_as_failed(tmp_path: Path) -> None:
    manager = _backup_manager(tmp_path)
    volume = _volume_record()

    manager._startup_helper_pod = Mock()  # type: ignore[method-assign]
    manager._execute_tar_archive = Mock()  # type: ignore[method-assign]
    manager._delete_helper_pod = Mock(side_effect=RuntimeError("cleanup refused"))  # type: ignore[method-assign]

    manager._copy_archive_from_helper_pod = Mock(  # type: ignore[method-assign]
        side_effect=lambda *, namespace, pod_name, remote_path, local_path: local_path.write_bytes(b"ok")
    )

    result = manager.backup_one(volume)

    assert result.status == "failed"
    assert result.backup_path is None
    assert result.checksum_sha256 is None
    assert "cleanup stage failed: cleanup refused" in result.message


def test_startup_helper_pod_with_non_retryable_create_error_raises_create_stage_error(tmp_path: Path) -> None:
    manager = _backup_manager(tmp_path, startup_retries=3)
    volume = _volume_record()

    manager._create_helper_pod = Mock(side_effect=ApiException(status=403, reason="forbidden"))  # type: ignore[method-assign]
    manager._delete_helper_pod = Mock()  # type: ignore[method-assign]

    with pytest.raises(BackupStageError, match="create stage failed"):
        manager._startup_helper_pod(volume=volume, pod_name="helper-pod")

    manager._create_helper_pod.assert_called_once()
    manager._delete_helper_pod.assert_not_called()


def test_startup_helper_pod_with_timeout_retries_then_raises_wait_stage_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _backup_manager(tmp_path, startup_retries=1)
    volume = _volume_record()

    manager._create_helper_pod = Mock()  # type: ignore[method-assign]
    manager._wait_for_pod_running = Mock(side_effect=TimeoutError("pod timed out"))  # type: ignore[method-assign]
    manager._delete_helper_pod = Mock()  # type: ignore[method-assign]
    monkeypatch.setattr("nerdy_k8s_volume_manager.backup.time.sleep", lambda _: None)

    with pytest.raises(BackupStageError, match=r"wait stage failed: pod timed out \(after 2 attempts\)"):
        manager._startup_helper_pod(volume=volume, pod_name="helper-pod")

    assert manager._create_helper_pod.call_count == 2
    assert manager._wait_for_pod_running.call_count == 2
    assert manager._delete_helper_pod.call_count == 1


def test_execute_tar_archive_with_stream_failure_raises_exec_stage_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _backup_manager(tmp_path)

    monkeypatch.setattr(
        "nerdy_k8s_volume_manager.backup.stream",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("exec transport failed")),
    )

    with pytest.raises(BackupStageError, match="exec stage failed: exec transport failed"):
        manager._execute_tar_archive(namespace="apps", pod_name="helper-pod", remote_path="/tmp/archive.tar.gz")


def test_validate_archive_and_checksum_with_missing_file_raises_checksum_stage_error(tmp_path: Path) -> None:
    manager = _backup_manager(tmp_path)

    with pytest.raises(BackupStageError, match="checksum stage failed: archive not found"):
        manager._validate_archive_and_checksum(local_archive_path=tmp_path / "missing.tar.gz")


def test_validate_archive_and_checksum_with_empty_archive_raises_checksum_stage_error(tmp_path: Path) -> None:
    manager = _backup_manager(tmp_path)
    archive_path = tmp_path / "empty.tar.gz"
    archive_path.write_bytes(b"")

    with pytest.raises(BackupStageError, match="checksum stage failed: archive is empty"):
        manager._validate_archive_and_checksum(local_archive_path=archive_path)


def test_create_helper_pod_submits_read_only_pvc_mount(tmp_path: Path) -> None:
    manager = _backup_manager(tmp_path)
    volume = _volume_record()

    manager._create_helper_pod(volume=volume, pod_name="helper-pod")

    manager.core_api.create_namespaced_pod.assert_called_once()
    kwargs = manager.core_api.create_namespaced_pod.call_args.kwargs
    pod_spec = kwargs["body"].spec
    assert kwargs["namespace"] == volume.namespace
    assert pod_spec.restart_policy == "Never"
    assert pod_spec.containers[0].volume_mounts[0].mount_path == "/data"
    assert pod_spec.containers[0].volume_mounts[0].read_only is True
    assert pod_spec.volumes[0].persistent_volume_claim.claim_name == volume.pvc_name


def test_create_helper_pod_pins_to_running_pvc_consumer_node(tmp_path: Path) -> None:
    manager = _backup_manager(tmp_path)
    volume = _volume_record()
    manager.core_api.list_namespaced_pod.return_value = SimpleNamespace(
        items=[
            SimpleNamespace(
                spec=SimpleNamespace(
                    node_name="worker-01",
                    volumes=[
                        SimpleNamespace(
                            persistent_volume_claim=SimpleNamespace(claim_name=volume.pvc_name)
                        )
                    ],
                ),
                status=SimpleNamespace(phase="Running"),
            )
        ]
    )

    manager._create_helper_pod(volume=volume, pod_name="helper-pod")

    pod_spec = manager.core_api.create_namespaced_pod.call_args.kwargs["body"].spec
    assert pod_spec.node_name == "worker-01"


def test_create_helper_pod_with_no_running_pvc_consumer_leaves_node_unset(tmp_path: Path) -> None:
    manager = _backup_manager(tmp_path)
    volume = _volume_record()
    manager.core_api.list_namespaced_pod.return_value = SimpleNamespace(
        items=[
            SimpleNamespace(
                spec=SimpleNamespace(
                    node_name="worker-02",
                    volumes=[
                        SimpleNamespace(
                            persistent_volume_claim=SimpleNamespace(claim_name=volume.pvc_name)
                        )
                    ],
                ),
                status=SimpleNamespace(phase="Pending"),
            )
        ]
    )

    manager._create_helper_pod(volume=volume, pod_name="helper-pod")

    pod_spec = manager.core_api.create_namespaced_pod.call_args.kwargs["body"].spec
    assert pod_spec.node_name is None


def test_create_helper_pod_with_existing_name_recreates_pod(tmp_path: Path) -> None:
    manager = _backup_manager(tmp_path)
    volume = _volume_record()
    manager.core_api.create_namespaced_pod.side_effect = [ApiException(status=409, reason="exists"), None]
    manager._delete_helper_pod = Mock()  # type: ignore[method-assign]

    manager._create_helper_pod(volume=volume, pod_name="helper-pod")

    assert manager.core_api.create_namespaced_pod.call_count == 2
    manager._delete_helper_pod.assert_called_once_with(namespace=volume.namespace, pod_name="helper-pod")


def test_wait_for_pod_running_with_running_phase_returns_immediately(tmp_path: Path) -> None:
    manager = _backup_manager(tmp_path)
    manager.core_api.read_namespaced_pod.return_value = SimpleNamespace(
        status=SimpleNamespace(phase="Running")
    )

    manager._wait_for_pod_running(namespace="apps", pod_name="helper-pod")


def test_wait_for_pod_running_with_failed_phase_raises_runtime_error(tmp_path: Path) -> None:
    manager = _backup_manager(tmp_path)
    manager.core_api.read_namespaced_pod.return_value = SimpleNamespace(
        status=SimpleNamespace(phase="Failed")
    )

    with pytest.raises(RuntimeError, match="unexpected phase: Failed"):
        manager._wait_for_pod_running(namespace="apps", pod_name="helper-pod")


def test_wait_for_pod_running_with_timeout_raises_timeout_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _backup_manager(tmp_path)
    manager.core_api.read_namespaced_pod.return_value = SimpleNamespace(
        status=SimpleNamespace(phase="Pending")
    )

    time_values = iter([0, 1, 999])
    monkeypatch.setattr("nerdy_k8s_volume_manager.backup.time.time", lambda: next(time_values, 999))
    monkeypatch.setattr("nerdy_k8s_volume_manager.backup.time.sleep", lambda _: None)

    with pytest.raises(TimeoutError, match="did not become Running in time"):
        manager._wait_for_pod_running(namespace="apps", pod_name="helper-pod")


def test_wait_for_pod_running_timeout_includes_pending_hint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _backup_manager(tmp_path)
    manager.core_api.read_namespaced_pod.return_value = SimpleNamespace(
        status=SimpleNamespace(
            phase="Pending",
            conditions=[
                SimpleNamespace(
                    type="PodScheduled",
                    status="False",
                    reason="Unschedulable",
                    message="0/3 nodes are available: 3 Insufficient cpu.",
                )
            ],
        )
    )

    time_values = iter([0, 1, 999])
    monkeypatch.setattr("nerdy_k8s_volume_manager.backup.time.time", lambda: next(time_values, 999))
    monkeypatch.setattr("nerdy_k8s_volume_manager.backup.time.sleep", lambda _: None)

    with pytest.raises(TimeoutError, match="pod unschedulable"):
        manager._wait_for_pod_running(namespace="apps", pod_name="helper-pod")


def test_delete_helper_pod_with_404_is_ignored(tmp_path: Path) -> None:
    manager = _backup_manager(tmp_path)
    manager.core_api.delete_namespaced_pod.side_effect = ApiException(status=404, reason="missing")

    manager._delete_helper_pod(namespace="apps", pod_name="helper-pod")


def test_delete_helper_pod_with_unexpected_api_error_is_raised(tmp_path: Path) -> None:
    manager = _backup_manager(tmp_path)
    manager.core_api.delete_namespaced_pod.side_effect = ApiException(status=500, reason="server-error")

    with pytest.raises(ApiException):
        manager._delete_helper_pod(namespace="apps", pod_name="helper-pod")


def test_kubectl_copy_from_pod_includes_optional_kubeconfig_and_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kubeconfig_path = tmp_path / "test-kubeconfig.yaml"
    kubeconfig_path.write_text("apiVersion: v1\nkind: Config\n", encoding="utf-8")

    manager = BackupManager(
        core_api=Mock(),
        metadata_store=Mock(),
        config=BackupManagerConfig(
            backup_dir=tmp_path,
            helper_image="alpine:3.20",
            helper_pod_timeout_seconds=15,
            kubeconfig_path=str(kubeconfig_path),
            context="dev-cluster",
            helper_pod_startup_retries=0,
        ),
    )
    captured_command: dict[str, list[str]] = {}

    def _fake_run(command: list[str], *, check: bool, capture_output: bool, text: bool) -> subprocess.CompletedProcess[str]:
        assert check is False
        assert capture_output is True
        assert text is True
        captured_command["value"] = command
        return subprocess.CompletedProcess(args=command, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("nerdy_k8s_volume_manager.backup.shutil.which", lambda _: "/usr/bin/kubectl")
    monkeypatch.setattr("nerdy_k8s_volume_manager.backup.subprocess.run", _fake_run)

    manager._kubectl_copy_from_pod(
        namespace="apps",
        pod_name="helper-pod",
        remote_path="/tmp/archive.tar.gz",
        local_path=tmp_path / "archive.tar.gz",
    )

    command = captured_command["value"]
    assert "--kubeconfig" in command
    assert str(kubeconfig_path) in command
    assert "--context" in command
    assert "dev-cluster" in command


def test_kubectl_copy_from_pod_with_in_cluster_auth_generates_kubeconfig_and_ignores_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = BackupManager(
        core_api=Mock(),
        metadata_store=Mock(),
        config=BackupManagerConfig(
            backup_dir=tmp_path,
            helper_image="alpine:3.20",
            helper_pod_timeout_seconds=15,
            kubeconfig_path=None,
            context="ignored-context",
            in_cluster_auth=True,
            helper_pod_startup_retries=0,
        ),
    )
    generated_kubeconfig_path = tmp_path / "generated-incluster.yaml"
    generated_kubeconfig_path.write_text("apiVersion: v1\n", encoding="utf-8")
    captured_command: dict[str, list[str]] = {}

    def _fake_run(command: list[str], *, check: bool, capture_output: bool, text: bool) -> subprocess.CompletedProcess[str]:
        assert check is False
        assert capture_output is True
        assert text is True
        captured_command["value"] = command
        return subprocess.CompletedProcess(args=command, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("nerdy_k8s_volume_manager.backup._build_incluster_kubeconfig", lambda: generated_kubeconfig_path)
    monkeypatch.setattr("nerdy_k8s_volume_manager.backup.shutil.which", lambda _: "/usr/bin/kubectl")
    monkeypatch.setattr("nerdy_k8s_volume_manager.backup.subprocess.run", _fake_run)

    manager._kubectl_copy_from_pod(
        namespace="apps",
        pod_name="helper-pod",
        remote_path="/tmp/archive.tar.gz",
        local_path=tmp_path / "archive.tar.gz",
    )

    command = captured_command["value"]
    assert "--kubeconfig" in command
    assert str(generated_kubeconfig_path) in command
    assert "--context" not in command
    assert "ignored-context" not in command
    assert generated_kubeconfig_path.exists() is False


def test_build_incluster_kubeconfig_renders_expected_server_and_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token_path = tmp_path / "token"
    ca_path = tmp_path / "ca.crt"
    token_path.write_text("token", encoding="utf-8")
    ca_path.write_text("ca", encoding="utf-8")

    monkeypatch.setattr("nerdy_k8s_volume_manager.backup.SERVICEACCOUNT_TOKEN_PATH", token_path)
    monkeypatch.setattr("nerdy_k8s_volume_manager.backup.SERVICEACCOUNT_CA_PATH", ca_path)
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "10.96.0.1")
    monkeypatch.setenv("KUBERNETES_SERVICE_PORT_HTTPS", "6443")

    kubeconfig_path = _build_incluster_kubeconfig()
    try:
        content = kubeconfig_path.read_text(encoding="utf-8")
        assert "server: https://10.96.0.1:6443" in content
        assert f"certificate-authority: {ca_path}" in content
        assert f"tokenFile: {token_path}" in content
    finally:
        kubeconfig_path.unlink(missing_ok=True)


def test_build_incluster_kubeconfig_without_service_host_raises_actionable_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token_path = tmp_path / "token"
    ca_path = tmp_path / "ca.crt"
    token_path.write_text("token", encoding="utf-8")
    ca_path.write_text("ca", encoding="utf-8")

    monkeypatch.setattr("nerdy_k8s_volume_manager.backup.SERVICEACCOUNT_TOKEN_PATH", token_path)
    monkeypatch.setattr("nerdy_k8s_volume_manager.backup.SERVICEACCOUNT_CA_PATH", ca_path)
    monkeypatch.delenv("KUBERNETES_SERVICE_HOST", raising=False)

    with pytest.raises(RuntimeError, match="KUBERNETES_SERVICE_HOST"):
        _build_incluster_kubeconfig()


def test_build_incluster_kubeconfig_without_service_account_token_raises_actionable_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token_path = tmp_path / "missing-token"
    ca_path = tmp_path / "ca.crt"
    ca_path.write_text("ca", encoding="utf-8")

    monkeypatch.setattr("nerdy_k8s_volume_manager.backup.SERVICEACCOUNT_TOKEN_PATH", token_path)
    monkeypatch.setattr("nerdy_k8s_volume_manager.backup.SERVICEACCOUNT_CA_PATH", ca_path)
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "10.96.0.1")

    with pytest.raises(RuntimeError, match="service account token"):
        _build_incluster_kubeconfig()


def test_build_incluster_kubeconfig_without_ca_bundle_raises_actionable_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token_path = tmp_path / "token"
    ca_path = tmp_path / "missing-ca"
    token_path.write_text("token", encoding="utf-8")

    monkeypatch.setattr("nerdy_k8s_volume_manager.backup.SERVICEACCOUNT_TOKEN_PATH", token_path)
    monkeypatch.setattr("nerdy_k8s_volume_manager.backup.SERVICEACCOUNT_CA_PATH", ca_path)
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "10.96.0.1")

    with pytest.raises(RuntimeError, match="service account CA bundle"):
        _build_incluster_kubeconfig()


def test_kubectl_copy_from_pod_with_missing_kubeconfig_path_raises_actionable_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = BackupManager(
        core_api=Mock(),
        metadata_store=Mock(),
        config=BackupManagerConfig(
            backup_dir=tmp_path,
            helper_image="alpine:3.20",
            helper_pod_timeout_seconds=15,
            kubeconfig_path=str(tmp_path / "missing-config"),
            context="dev-cluster",
            helper_pod_startup_retries=0,
        ),
    )

    monkeypatch.setattr("nerdy_k8s_volume_manager.backup.shutil.which", lambda _: "/usr/bin/kubectl")

    with pytest.raises(RuntimeError, match="kubeconfig path does not exist"):
        manager._kubectl_copy_from_pod(
            namespace="apps",
            pod_name="helper-pod",
            remote_path="/tmp/archive.tar.gz",
            local_path=tmp_path / "archive.tar.gz",
        )


def test_upload_archive_to_remote_destination_with_ftp_creates_directories_and_uploads_archive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = BackupManager(
        core_api=Mock(),
        metadata_store=Mock(),
        config=BackupManagerConfig(
            backup_dir=tmp_path,
            helper_image="alpine:3.20",
            helper_pod_timeout_seconds=15,
            kubeconfig_path=None,
            context=None,
            destination_mode="remote",
            remote_destination=RemoteDestinationConfig(
                protocol="ftp",
                host="backup.internal",
                username="svc-backup",
                password="secret",
                directory="/archives/daily",
            ),
        ),
    )
    local_archive = tmp_path / "archive.tar.gz"
    local_archive.write_bytes(b"archive-payload")

    class FakeFTP:
        last_instance: "FakeFTP | None" = None

        def __init__(self, timeout: int = 60) -> None:
            self.timeout = timeout
            self.connected: tuple[str, int] | None = None
            self.logged_in: tuple[str, str] | None = None
            self.current_dir = "/"
            self.known_directories = {"/"}
            self.mkdir_calls: list[str] = []
            self.upload_command: str | None = None
            self.upload_payload = b""
            FakeFTP.last_instance = self

        def __enter__(self) -> "FakeFTP":
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def connect(self, host: str, port: int) -> None:
            self.connected = (host, port)

        def login(self, user: str, passwd: str) -> None:
            self.logged_in = (user, passwd)

        def cwd(self, path: str) -> None:
            if path == "/":
                self.current_dir = "/"
                return

            target = path if path.startswith("/") else f"{self.current_dir.rstrip('/')}/{path}"
            if target not in self.known_directories:
                raise ftplib.error_perm("550 missing directory")
            self.current_dir = target

        def mkd(self, path: str) -> None:
            target = path if path.startswith("/") else f"{self.current_dir.rstrip('/')}/{path}"
            self.known_directories.add(target)
            self.mkdir_calls.append(target)

        def storbinary(self, command: str, file_handle) -> None:
            self.upload_command = command
            self.upload_payload = file_handle.read()

    monkeypatch.setattr("nerdy_k8s_volume_manager.backup.ftplib.FTP", FakeFTP)

    remote_path = manager._upload_archive_to_remote_destination(local_archive_path=local_archive)

    ftp_instance = FakeFTP.last_instance
    assert ftp_instance is not None
    assert ftp_instance.connected == ("backup.internal", 21)
    assert ftp_instance.logged_in == ("svc-backup", "secret")
    assert ftp_instance.mkdir_calls == ["/archives", "/archives/daily"]
    assert ftp_instance.upload_command == "STOR archive.tar.gz"
    assert ftp_instance.upload_payload == b"archive-payload"
    assert remote_path == "ftp://backup.internal/archives/daily/archive.tar.gz"


def test_upload_archive_to_remote_destination_with_ftps_enables_data_channel_protection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = BackupManager(
        core_api=Mock(),
        metadata_store=Mock(),
        config=BackupManagerConfig(
            backup_dir=tmp_path,
            helper_image="alpine:3.20",
            helper_pod_timeout_seconds=15,
            kubeconfig_path=None,
            context=None,
            destination_mode="remote",
            remote_destination=RemoteDestinationConfig(
                protocol="ftps",
                host="backup.internal",
                username="svc-backup",
                password="secret",
                directory="/",
            ),
        ),
    )
    local_archive = tmp_path / "archive.tar.gz"
    local_archive.write_bytes(b"archive-payload")

    class FakeFTPS:
        last_instance: "FakeFTPS | None" = None

        def __init__(self, timeout: int = 60) -> None:
            self.timeout = timeout
            self.current_dir = "/"
            self.known_directories = {"/"}
            self.prot_p_called = False
            FakeFTPS.last_instance = self

        def __enter__(self) -> "FakeFTPS":
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def connect(self, host: str, port: int) -> None:
            return None

        def login(self, user: str, passwd: str) -> None:
            return None

        def prot_p(self) -> None:
            self.prot_p_called = True

        def cwd(self, path: str) -> None:
            if path == "/":
                self.current_dir = "/"
                return
            raise ftplib.error_perm("550 missing directory")

        def mkd(self, path: str) -> None:
            return None

        def storbinary(self, command: str, file_handle) -> None:
            file_handle.read()

    monkeypatch.setattr("nerdy_k8s_volume_manager.backup.ftplib.FTP_TLS", FakeFTPS)

    remote_path = manager._upload_archive_to_remote_destination(local_archive_path=local_archive)

    ftps_instance = FakeFTPS.last_instance
    assert ftps_instance is not None
    assert ftps_instance.prot_p_called is True
    assert remote_path == "ftps://backup.internal/archive.tar.gz"


def test_upload_archive_to_remote_destination_with_unsupported_protocol_raises_remote_stage_error(
    tmp_path: Path,
) -> None:
    manager = BackupManager(
        core_api=Mock(),
        metadata_store=Mock(),
        config=BackupManagerConfig(
            backup_dir=tmp_path,
            helper_image="alpine:3.20",
            helper_pod_timeout_seconds=15,
            kubeconfig_path=None,
            context=None,
            destination_mode="remote",
            remote_destination=RemoteDestinationConfig(
                protocol="s3",
                host="backup.internal",
                username="svc-backup",
                password="secret",
                directory="/archives",
            ),
        ),
    )
    local_archive = tmp_path / "archive.tar.gz"
    local_archive.write_bytes(b"archive-payload")

    with pytest.raises(BackupStageError, match="remote stage failed: unsupported remote protocol"):
        manager._upload_archive_to_remote_destination(local_archive_path=local_archive)


def test_upload_archive_to_remote_destination_with_scp_runs_mkdir_and_scp_commands(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = BackupManager(
        core_api=Mock(),
        metadata_store=Mock(),
        config=BackupManagerConfig(
            backup_dir=tmp_path,
            helper_image="alpine:3.20",
            helper_pod_timeout_seconds=15,
            kubeconfig_path=None,
            context=None,
            destination_mode="remote",
            remote_destination=RemoteDestinationConfig(
                protocol="scp",
                host="backup.internal",
                username="svc-backup",
                password="secret",
                directory="/archives/daily",
            ),
        ),
    )
    local_archive = tmp_path / "archive.tar.gz"
    local_archive.write_bytes(b"archive-payload")

    binaries = {
        "sshpass": "/usr/bin/sshpass",
        "ssh": "/usr/bin/ssh",
        "scp": "/usr/bin/scp",
    }
    monkeypatch.setattr("nerdy_k8s_volume_manager.backup.shutil.which", lambda name: binaries.get(name))
    captured_commands: list[tuple[list[str], dict[str, str]]] = []

    def _fake_run(
        command: list[str],
        *,
        check: bool,
        capture_output: bool,
        text: bool,
        env: dict[str, str],
    ) -> subprocess.CompletedProcess[str]:
        assert check is False
        assert capture_output is True
        assert text is True
        captured_commands.append((command, env))
        return subprocess.CompletedProcess(args=command, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("nerdy_k8s_volume_manager.backup.subprocess.run", _fake_run)

    remote_path = manager._upload_archive_to_remote_destination(local_archive_path=local_archive)

    assert len(captured_commands) == 2
    mkdir_command, mkdir_env = captured_commands[0]
    scp_command, scp_env = captured_commands[1]

    assert mkdir_command[:3] == ["/usr/bin/sshpass", "-e", "/usr/bin/ssh"]
    assert mkdir_command[-2] == "svc-backup@backup.internal"
    assert mkdir_command[-1] == "mkdir -p /archives/daily"
    assert mkdir_env["SSHPASS"] == "secret"

    assert scp_command[:3] == ["/usr/bin/sshpass", "-e", "/usr/bin/scp"]
    assert scp_command[-2] == str(local_archive)
    assert scp_command[-1] == "svc-backup@backup.internal:/archives/daily/archive.tar.gz"
    assert scp_env["SSHPASS"] == "secret"

    assert remote_path == "scp://backup.internal/archives/daily/archive.tar.gz"


def test_upload_archive_to_remote_destination_with_rsync_runs_mkdir_and_rsync_commands(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = BackupManager(
        core_api=Mock(),
        metadata_store=Mock(),
        config=BackupManagerConfig(
            backup_dir=tmp_path,
            helper_image="alpine:3.20",
            helper_pod_timeout_seconds=15,
            kubeconfig_path=None,
            context=None,
            destination_mode="remote",
            remote_destination=RemoteDestinationConfig(
                protocol="rsync",
                host="backup.internal",
                username="svc-backup",
                password="secret",
                directory="/archives/daily",
            ),
        ),
    )
    local_archive = tmp_path / "archive.tar.gz"
    local_archive.write_bytes(b"archive-payload")

    binaries = {
        "sshpass": "/usr/bin/sshpass",
        "ssh": "/usr/bin/ssh",
        "rsync": "/usr/bin/rsync",
    }
    monkeypatch.setattr("nerdy_k8s_volume_manager.backup.shutil.which", lambda name: binaries.get(name))
    captured_commands: list[tuple[list[str], dict[str, str]]] = []

    def _fake_run(
        command: list[str],
        *,
        check: bool,
        capture_output: bool,
        text: bool,
        env: dict[str, str],
    ) -> subprocess.CompletedProcess[str]:
        assert check is False
        assert capture_output is True
        assert text is True
        captured_commands.append((command, env))
        return subprocess.CompletedProcess(args=command, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("nerdy_k8s_volume_manager.backup.subprocess.run", _fake_run)

    remote_path = manager._upload_archive_to_remote_destination(local_archive_path=local_archive)

    assert len(captured_commands) == 2
    mkdir_command, mkdir_env = captured_commands[0]
    rsync_command, rsync_env = captured_commands[1]

    assert mkdir_command[:3] == ["/usr/bin/sshpass", "-e", "/usr/bin/ssh"]
    assert mkdir_command[-2] == "svc-backup@backup.internal"
    assert mkdir_command[-1] == "mkdir -p /archives/daily"
    assert mkdir_env["SSHPASS"] == "secret"

    assert rsync_command[:3] == ["/usr/bin/sshpass", "-e", "/usr/bin/rsync"]
    assert "-e" in rsync_command
    assert "ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null" in rsync_command
    assert rsync_command[-2] == str(local_archive)
    assert rsync_command[-1] == "svc-backup@backup.internal:/archives/daily/archive.tar.gz"
    assert rsync_env["SSHPASS"] == "secret"

    assert remote_path == "rsync://backup.internal/archives/daily/archive.tar.gz"


def test_upload_archive_to_remote_destination_with_scp_without_sshpass_returns_remote_stage_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = BackupManager(
        core_api=Mock(),
        metadata_store=Mock(),
        config=BackupManagerConfig(
            backup_dir=tmp_path,
            helper_image="alpine:3.20",
            helper_pod_timeout_seconds=15,
            kubeconfig_path=None,
            context=None,
            destination_mode="remote",
            remote_destination=RemoteDestinationConfig(
                protocol="scp",
                host="backup.internal",
                username="svc-backup",
                password="secret",
                directory="/archives/daily",
            ),
        ),
    )
    local_archive = tmp_path / "archive.tar.gz"
    local_archive.write_bytes(b"archive-payload")

    binaries = {
        "sshpass": None,
        "ssh": "/usr/bin/ssh",
        "scp": "/usr/bin/scp",
    }
    monkeypatch.setattr("nerdy_k8s_volume_manager.backup.shutil.which", lambda name: binaries.get(name))

    with pytest.raises(BackupStageError, match="remote stage failed: sshpass is required"):
        manager._upload_archive_to_remote_destination(local_archive_path=local_archive)


def test_retryable_startup_error_classification_and_error_message_fallback() -> None:
    assert _is_retryable_startup_error(TimeoutError("timeout")) is True
    assert _is_retryable_startup_error(ApiException(status=500, reason="server-error")) is True
    assert _is_retryable_startup_error(ApiException(status=403, reason="forbidden")) is False
    assert _is_retryable_startup_error(ValueError("invalid")) is False
    assert _error_message(Exception()) == "Exception"
