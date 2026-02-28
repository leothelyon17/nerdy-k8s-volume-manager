from __future__ import annotations

from pathlib import Path
import tarfile
from typing import TYPE_CHECKING

from kubernetes.config import incluster_config
import pytest

from nerdy_k8s_volume_manager.backup import BackupManager, BackupManagerConfig
from nerdy_k8s_volume_manager.k8s import (
    KubernetesAuthenticationError,
    KubernetesClients,
    KubernetesDiscoveryError,
    list_volume_records,
    load_kubernetes_clients,
)
from nerdy_k8s_volume_manager.metadata import BackupMetadataStore
from nerdy_k8s_volume_manager.models import BackupResult, VolumeRecord

if TYPE_CHECKING:
    from _pytest.monkeypatch import MonkeyPatch

    from .conftest import InClusterAuthMaterial, KindClusterContext

pytestmark = pytest.mark.integration


def _load_records(
    cluster: "KindClusterContext",
    *,
    kubeconfig_path: str | None,
    in_cluster: bool,
) -> tuple[KubernetesClients, list[VolumeRecord]]:
    clients = load_kubernetes_clients(
        kubeconfig_path=kubeconfig_path,
        context=None,
        in_cluster=in_cluster,
    )
    try:
        records = list_volume_records(
            clients,
            namespaces=[cluster.namespace],
            request_timeout_seconds=30,
            max_namespace_scan=10,
        )
        return clients, records
    except Exception:
        clients.api_client.close()
        raise


def _find_target_volume(cluster: "KindClusterContext", records: list[VolumeRecord]) -> VolumeRecord | None:
    return next(
        (
            record
            for record in records
            if record.namespace == cluster.namespace and record.pvc_name == cluster.pvc_name
        ),
        None,
    )


def _require_target_volume(cluster: "KindClusterContext", records: list[VolumeRecord]) -> VolumeRecord:
    target = _find_target_volume(cluster, records)
    if target is not None:
        return target

    discovered = [f"{record.namespace}/{record.pvc_name}" for record in records]
    pytest.fail(
        "KinD discovery did not return the seeded PVC.\n"
        f"Expected: {cluster.namespace}/{cluster.pvc_name}\n"
        f"Discovered PVCs: {discovered}\n"
        f"Diagnostics:\n{cluster.collect_diagnostics()}"
    )


def _assert_successful_archive(result: BackupResult) -> Path:
    assert result.status == "success"
    assert result.backup_path is not None

    archive_path = Path(result.backup_path)
    assert archive_path.exists(), f"Expected archive at {archive_path}."
    assert archive_path.stat().st_size > 0
    assert result.checksum_sha256 is not None

    with tarfile.open(archive_path, "r:gz") as archive:
        archive_names = archive.getnames()
        hello_member = next((name for name in archive_names if name.endswith("hello.txt")), None)
        assert hello_member is not None, f"Archive contents: {archive_names}"

        hello_file = archive.extractfile(hello_member)
        assert hello_file is not None
        assert hello_file.read().decode("utf-8").strip() == "nkvm integration smoke payload"

    return archive_path


def _configure_incluster_runtime_environment(
    *,
    monkeypatch: "MonkeyPatch",
    auth_material: "InClusterAuthMaterial",
) -> None:
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", auth_material.service_host)
    monkeypatch.setenv("KUBERNETES_SERVICE_PORT_HTTPS", auth_material.service_port)
    monkeypatch.setenv("KUBERNETES_SERVICE_PORT", auth_material.service_port)
    monkeypatch.setattr(
        "nerdy_k8s_volume_manager.backup.SERVICEACCOUNT_TOKEN_PATH",
        auth_material.token_path,
    )
    monkeypatch.setattr(
        "nerdy_k8s_volume_manager.backup.SERVICEACCOUNT_CA_PATH",
        auth_material.ca_path,
    )
    monkeypatch.setattr(
        incluster_config,
        "SERVICE_TOKEN_FILENAME",
        str(auth_material.token_path),
    )
    monkeypatch.setattr(
        incluster_config,
        "SERVICE_CERT_FILENAME",
        str(auth_material.ca_path),
    )


def test_kind_discovery_with_remote_kubeconfig_secret_lists_seeded_pvc(
    kind_cluster: "KindClusterContext",
) -> None:
    clients, records = _load_records(
        kind_cluster,
        kubeconfig_path=str(kind_cluster.remote_kubeconfig_secret_path),
        in_cluster=False,
    )
    try:
        target = _require_target_volume(kind_cluster, records)
    finally:
        clients.api_client.close()

    assert target.phase == "Bound"
    assert target.pvc_uid != ""
    assert target.app_kind in {"Pod", "Unknown", "Multiple[Pod]"}


def test_kind_backup_with_remote_kubeconfig_secret_creates_archive_and_persists_metadata(
    kind_cluster: "KindClusterContext",
    tmp_path: Path,
) -> None:
    clients, records = _load_records(
        kind_cluster,
        kubeconfig_path=str(kind_cluster.remote_kubeconfig_secret_path),
        in_cluster=False,
    )
    metadata_store = BackupMetadataStore(tmp_path / "backups.db")
    metadata_store.initialize()

    try:
        target = _require_target_volume(kind_cluster, records)

        manager = BackupManager(
            core_api=clients.core_api,
            metadata_store=metadata_store,
            config=BackupManagerConfig(
                backup_dir=tmp_path / "archives",
                helper_image="alpine:3.20",
                helper_pod_timeout_seconds=180,
                kubeconfig_path=str(kind_cluster.remote_kubeconfig_secret_path),
                context=None,
                helper_pod_startup_retries=1,
            ),
        )

        result = manager.backup_one(target)
        metadata_store.record_result(result)
    finally:
        clients.api_client.close()

    if result.status != "success" or result.backup_path is None:
        pytest.fail(
            "KinD backup smoke test (remote kubeconfig secret path) failed.\n"
            f"Backup result: {result}\n"
            f"Diagnostics:\n{kind_cluster.collect_diagnostics()}"
        )

    _assert_successful_archive(result)

    last_success = metadata_store.get_last_success_map()
    assert (kind_cluster.namespace, kind_cluster.pvc_name) in last_success
    assert last_success[(kind_cluster.namespace, kind_cluster.pvc_name)] == result.finished_at

    recent_rows = metadata_store.get_recent_results(limit=1)
    assert len(recent_rows) == 1
    assert recent_rows[0]["status"] == "success"
    assert recent_rows[0]["backup_path"] == result.backup_path
    assert recent_rows[0]["checksum_sha256"] == result.checksum_sha256


def test_kind_backup_with_incluster_serviceaccount_auth_creates_archive(
    kind_cluster: "KindClusterContext",
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    auth_material = kind_cluster.build_incluster_auth_material(
        service_account_name=kind_cluster.runtime_service_account,
    )
    _configure_incluster_runtime_environment(monkeypatch=monkeypatch, auth_material=auth_material)

    clients, records = _load_records(
        kind_cluster,
        kubeconfig_path=None,
        in_cluster=True,
    )
    metadata_store = BackupMetadataStore(tmp_path / "incluster.db")
    metadata_store.initialize()

    try:
        target = _require_target_volume(kind_cluster, records)

        manager = BackupManager(
            core_api=clients.core_api,
            metadata_store=metadata_store,
            config=BackupManagerConfig(
                backup_dir=tmp_path / "incluster-archives",
                helper_image="alpine:3.20",
                helper_pod_timeout_seconds=180,
                kubeconfig_path=None,
                context="ignored-incluster-context",
                in_cluster_auth=True,
                helper_pod_startup_retries=1,
            ),
        )

        result = manager.backup_one(target)
    finally:
        clients.api_client.close()

    if result.status != "success" or result.backup_path is None:
        pytest.fail(
            "KinD backup smoke test (in-cluster ServiceAccount auth mode) failed.\n"
            f"Backup result: {result}\n"
            f"Service account: {auth_material.service_account_name}\n"
            f"Diagnostics:\n{kind_cluster.collect_diagnostics()}"
        )

    _assert_successful_archive(result)


def test_kind_auth_failure_with_invalid_remote_kubeconfig_is_explicit(
    kind_cluster: "KindClusterContext",
) -> None:
    invalid_kubeconfig = kind_cluster.harness_dir / "missing" / "remote-kubeconfig"

    with pytest.raises(KubernetesAuthenticationError) as error_info:
        load_kubernetes_clients(
            kubeconfig_path=str(invalid_kubeconfig),
            context="missing-context",
            in_cluster=False,
        )

    message = str(error_info.value)
    assert "Kubernetes authentication setup failed" in message
    assert str(invalid_kubeconfig) in message


def test_kind_rbac_failure_with_unbound_serviceaccount_is_explicit(
    kind_cluster: "KindClusterContext",
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth_material = kind_cluster.build_incluster_auth_material(
        service_account_name=kind_cluster.unbound_service_account,
    )
    _configure_incluster_runtime_environment(monkeypatch=monkeypatch, auth_material=auth_material)

    clients = load_kubernetes_clients(
        kubeconfig_path=None,
        context=None,
        in_cluster=True,
    )
    try:
        with pytest.raises(KubernetesDiscoveryError) as error_info:
            list_volume_records(
                clients,
                namespaces=[kind_cluster.namespace],
                request_timeout_seconds=30,
                max_namespace_scan=10,
            )
    finally:
        clients.api_client.close()

    message = str(error_info.value)
    assert "list PVCs in namespace" in message
    assert "403" in message, f"Expected Forbidden RBAC signal.\nDiagnostics:\n{kind_cluster.collect_diagnostics()}"
    assert "forbidden" in message.lower()


def test_kind_backup_copy_stage_failure_with_missing_remote_kubeconfig_is_explicit(
    kind_cluster: "KindClusterContext",
    tmp_path: Path,
) -> None:
    clients, records = _load_records(
        kind_cluster,
        kubeconfig_path=str(kind_cluster.remote_kubeconfig_secret_path),
        in_cluster=False,
    )

    try:
        target = _require_target_volume(kind_cluster, records)

        manager = BackupManager(
            core_api=clients.core_api,
            metadata_store=BackupMetadataStore(tmp_path / "copy-failure.db"),
            config=BackupManagerConfig(
                backup_dir=tmp_path / "copy-failure-archives",
                helper_image="alpine:3.20",
                helper_pod_timeout_seconds=180,
                kubeconfig_path=str(kind_cluster.harness_dir / "missing" / "config"),
                context=None,
                helper_pod_startup_retries=1,
            ),
        )

        result = manager.backup_one(target)
    finally:
        clients.api_client.close()

    assert result.status == "failed"
    assert "copy stage failed" in result.message
    assert "kubeconfig path does not exist" in result.message
