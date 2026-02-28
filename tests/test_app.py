from __future__ import annotations

from pathlib import Path

from nerdy_k8s_volume_manager.app import (
    _AUTH_MODE_IN_CLUSTER,
    _AUTH_MODE_PASTE_KUBECONFIG,
    _AUTH_MODE_USE_KUBECONFIG_PATH,
    _BATCH_MODE_PARALLEL_LABEL,
    _BATCH_MODE_SEQUENTIAL_LABEL,
    _DESTINATION_LOCAL_LABEL,
    _DESTINATION_REMOTE_LABEL,
    _REMOTE_PROTOCOL_FTP_LABEL,
    _REMOTE_PROTOCOL_FTPS_LABEL,
    _REMOTE_PROTOCOL_SCP_LABEL,
    _REMOTE_PROTOCOL_RSYNC_LABEL,
    _auth_mode_guidance,
    _build_batch_execution_settings,
    _default_auth_mode,
    _build_history_rows,
    _build_result_rows,
    _build_volume_rows,
    _build_workflow_rows,
    _validate_connection_inputs,
    _validate_remote_destination_inputs,
    _remote_protocol_value,
)
from nerdy_k8s_volume_manager.models import BackupResult, VolumeRecord


def _volume_record() -> VolumeRecord:
    return VolumeRecord(
        namespace="apps",
        pvc_name="db-data",
        pvc_uid="pvc-uid-123",
        phase="Bound",
        capacity=None,
        storage_class=None,
        access_modes=("ReadWriteOnce", "ReadOnlyMany"),
        bound_pv=None,
        app_kind=None,
        app_name=None,
        last_successful_backup_at=None,
    )


def _failed_result() -> BackupResult:
    return BackupResult(
        namespace="apps",
        pvc_name="db-data",
        pvc_uid="pvc-uid-123",
        status="failed",
        started_at="2026-02-23T10:00:00+00:00",
        finished_at="2026-02-23T10:01:00+00:00",
        backup_path=None,
        checksum_sha256=None,
        message="copy stage failed: kubectl cp failed",
    )


def _valid_kubeconfig_content() -> str:
    return """
apiVersion: v1
clusters:
  - name: dev
    cluster:
      server: https://example.invalid
contexts:
  - name: dev
    context:
      cluster: dev
      user: dev
users:
  - name: dev
    user:
      token: abc
current-context: dev
"""


def test_build_volume_rows_with_missing_optional_fields_returns_unknown_defaults() -> None:
    rows = _build_volume_rows([_volume_record()])

    assert len(rows) == 1
    assert rows[0]["app"] == "Unknown/Unknown"
    assert rows[0]["capacity"] == "unknown"
    assert rows[0]["storage_class"] == "unknown"
    assert rows[0]["bound_pv"] == "unknown"
    assert rows[0]["access_modes"] == "ReadWriteOnce,ReadOnlyMany"


def test_build_result_rows_with_failed_stage_includes_actionable_next_step() -> None:
    rows = _build_result_rows([_failed_result()])

    assert len(rows) == 1
    assert rows[0]["status"] == "failed"
    assert "copy stage failed" in rows[0]["actionable_message"]
    assert "Check local kubectl availability" in rows[0]["actionable_message"]


def test_build_result_rows_with_remote_stage_failure_includes_remote_hint() -> None:
    rows = _build_result_rows(
        [
            BackupResult(
                namespace="apps",
                pvc_name="db-data",
                pvc_uid="pvc-uid-123",
                status="failed",
                started_at="2026-02-23T10:00:00+00:00",
                finished_at="2026-02-23T10:01:00+00:00",
                message="remote stage failed: login failed",
            )
        ]
    )

    assert len(rows) == 1
    assert "Validate remote protocol" in rows[0]["actionable_message"]


def test_build_history_rows_with_success_and_failure_sets_expected_actionable_messages() -> None:
    rows = _build_history_rows(
        [
            {
                "namespace": "apps",
                "pvc_name": "db-data",
                "status": "success",
                "backup_path": "/tmp/db-data.tar.gz",
                "checksum_sha256": "abc",
                "message": "",
                "created_at": "2026-02-23T10:01:00+00:00",
            },
            {
                "namespace": "apps",
                "pvc_name": "cache-data",
                "status": "failed",
                "backup_path": None,
                "checksum_sha256": None,
                "message": "wait stage failed: pod timed out",
                "created_at": "2026-02-23T10:02:00+00:00",
            },
        ]
    )

    assert len(rows) == 2
    assert rows[0]["actionable_message"] == "Backup completed successfully."
    assert "Inspect pod events" in rows[1]["actionable_message"]


def test_build_batch_execution_settings_with_parallel_preview_keeps_requested_workers() -> None:
    settings = _build_batch_execution_settings(
        mode_label=_BATCH_MODE_PARALLEL_LABEL,
        requested_max_workers=6,
        stop_on_failure=True,
    )

    assert settings.mode == "parallel_preview"
    assert settings.requested_max_workers == 6
    assert settings.effective_max_workers == 1
    assert settings.stop_on_failure is True


def test_build_batch_execution_settings_with_sequential_mode_forces_sequential_execution() -> None:
    settings = _build_batch_execution_settings(
        mode_label=_BATCH_MODE_SEQUENTIAL_LABEL,
        requested_max_workers=0,
        stop_on_failure=False,
    )

    assert settings.mode == "sequential"
    assert settings.requested_max_workers == 1
    assert settings.effective_max_workers == 1
    assert settings.stop_on_failure is False


def test_build_workflow_rows_with_discovery_selection_and_backup_completed_marks_steps_done() -> None:
    rows = _build_workflow_rows(
        connected=True,
        discovered_count=3,
        selected_count=2,
        backup_results_count=2,
    )

    assert [row["state"] for row in rows] == ["Done", "Done", "Done", "Done", "Done"]


def test_validate_connection_inputs_with_pasted_mode_and_missing_content_returns_error() -> None:
    error = _validate_connection_inputs(
        auth_mode="Paste kubeconfig",
        kubeconfig_path_input="",
        kubeconfig_text_input="",
    )

    assert error == "Paste kubeconfig content before connecting."


def test_validate_connection_inputs_with_existing_kubeconfig_path_returns_none(tmp_path: Path) -> None:
    kubeconfig_path = tmp_path / "config"
    kubeconfig_path.write_text(_valid_kubeconfig_content())

    error = _validate_connection_inputs(
        auth_mode="Use kubeconfig path",
        kubeconfig_path_input=str(kubeconfig_path),
        kubeconfig_text_input="",
    )

    assert error is None


def test_validate_connection_inputs_with_kubeconfig_path_directory_returns_error(tmp_path: Path) -> None:
    error = _validate_connection_inputs(
        auth_mode=_AUTH_MODE_USE_KUBECONFIG_PATH,
        kubeconfig_path_input=str(tmp_path),
        kubeconfig_text_input="",
    )

    assert error == f"Kubeconfig path must point to a file: {tmp_path}"


def test_validate_connection_inputs_with_pasted_invalid_yaml_returns_error() -> None:
    error = _validate_connection_inputs(
        auth_mode=_AUTH_MODE_PASTE_KUBECONFIG,
        kubeconfig_path_input="",
        kubeconfig_text_input="apiVersion: v1\nclusters: [",
    )

    assert error == "Pasted kubeconfig must be valid YAML: ParserError."


def test_validate_connection_inputs_with_pasted_missing_contexts_returns_error() -> None:
    error = _validate_connection_inputs(
        auth_mode=_AUTH_MODE_PASTE_KUBECONFIG,
        kubeconfig_path_input="",
        kubeconfig_text_input="""
apiVersion: v1
clusters: []
users: []
""",
    )

    assert error == "Pasted kubeconfig is missing required field(s): contexts."


def test_validate_connection_inputs_with_incluster_mode_without_pod_environment_returns_error(monkeypatch) -> None:
    monkeypatch.delenv("KUBERNETES_SERVICE_HOST", raising=False)
    monkeypatch.setattr("nerdy_k8s_volume_manager.app.Path.exists", lambda self: False)

    error = _validate_connection_inputs(
        auth_mode=_AUTH_MODE_IN_CLUSTER,
        kubeconfig_path_input="",
        kubeconfig_text_input="",
    )

    assert "In-cluster service account mode requires Kubernetes pod environment variables" in str(error)


def test_auth_mode_guidance_for_incluster_mentions_primary_serviceaccount_flow() -> None:
    guidance = _auth_mode_guidance(_AUTH_MODE_IN_CLUSTER)

    assert "Primary mode for in-cluster deployments" in guidance
    assert "ServiceAccount" in guidance


def test_auth_mode_guidance_for_kubeconfig_path_mentions_remote_cluster() -> None:
    guidance = _auth_mode_guidance(_AUTH_MODE_USE_KUBECONFIG_PATH)

    assert "remote cluster targets" in guidance
    assert "/etc/nkvm/remote/config" in guidance


def test_auth_mode_guidance_for_paste_mentions_troubleshooting_use_only() -> None:
    guidance = _auth_mode_guidance(_AUTH_MODE_PASTE_KUBECONFIG)

    assert "short-lived troubleshooting" in guidance
    assert "apiVersion, clusters, contexts, and users" in guidance


def test_default_auth_mode_prefers_env_override_then_incluster_detection(
    tmp_path: Path,
    monkeypatch,
) -> None:
    token_path = tmp_path / "token"
    token_path.write_text("token", encoding="utf-8")

    monkeypatch.delenv("NKVM_DEFAULT_AUTH_MODE", raising=False)
    monkeypatch.delenv("KUBERNETES_SERVICE_HOST", raising=False)
    monkeypatch.setattr(
        "nerdy_k8s_volume_manager.app.Path.exists",
        lambda self: False,
    )
    assert _default_auth_mode() == _AUTH_MODE_USE_KUBECONFIG_PATH

    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "10.96.0.1")
    monkeypatch.setattr(
        "nerdy_k8s_volume_manager.app.Path.exists",
        lambda self: str(self) == "/var/run/secrets/kubernetes.io/serviceaccount/token",
    )
    assert _default_auth_mode() == _AUTH_MODE_IN_CLUSTER

    monkeypatch.setenv("NKVM_DEFAULT_AUTH_MODE", "paste")
    assert _default_auth_mode() == _AUTH_MODE_PASTE_KUBECONFIG


def test_validate_remote_destination_inputs_with_missing_values_returns_all_errors() -> None:
    errors = _validate_remote_destination_inputs(
        host_input="",
        username_input="",
        password_input="",
        directory_input="",
    )

    assert errors == [
        "Remote destination IP/hostname is required.",
        "Remote destination username is required.",
        "Remote destination password is required.",
        "Remote destination directory is required.",
    ]


def test_validate_remote_destination_inputs_with_complete_values_returns_no_errors() -> None:
    errors = _validate_remote_destination_inputs(
        host_input="backup.internal",
        username_input="svc-backup",
        password_input="top-secret",
        directory_input="/archives",
    )

    assert errors == []


def test_remote_protocol_value_maps_expected_labels() -> None:
    assert _remote_protocol_value(_REMOTE_PROTOCOL_FTP_LABEL) == "ftp"
    assert _remote_protocol_value(_REMOTE_PROTOCOL_FTPS_LABEL) == "ftps"
    assert _remote_protocol_value(_REMOTE_PROTOCOL_SCP_LABEL) == "scp"
    assert _remote_protocol_value(_REMOTE_PROTOCOL_RSYNC_LABEL) == "rsync"
    assert _remote_protocol_value(_DESTINATION_LOCAL_LABEL) == "ftp"
    assert _DESTINATION_REMOTE_LABEL == "Remote destination"
