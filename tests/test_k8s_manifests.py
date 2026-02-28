from __future__ import annotations

from pathlib import Path

import yaml

_APP_MANIFEST_DIR = Path("deploy/k8s/app")


def _read_yaml_document(file_name: str) -> dict:
    return yaml.safe_load((_APP_MANIFEST_DIR / file_name).read_text(encoding="utf-8"))


def _deployment_manifest() -> dict:
    return _read_yaml_document("deployment.yaml")


def _deployment_container() -> dict:
    deployment = _deployment_manifest()
    return deployment["spec"]["template"]["spec"]["containers"][0]


def _container_env_map() -> dict[str, str]:
    env_entries = _deployment_container()["env"]
    return {entry["name"]: entry["value"] for entry in env_entries}


def _container_mount_map() -> dict[str, dict]:
    mounts = _deployment_container()["volumeMounts"]
    return {mount["name"]: mount for mount in mounts}


def _volume_map() -> dict[str, dict]:
    volumes = _deployment_manifest()["spec"]["template"]["spec"]["volumes"]
    return {volume["name"]: volume for volume in volumes}


def test_deployment_with_default_auth_env_sets_in_cluster_mode() -> None:
    env = _container_env_map()

    assert env["NKVM_DEFAULT_AUTH_MODE"] == "in-cluster"


def test_deployment_with_runtime_data_mount_aligns_with_backup_and_metadata_paths() -> None:
    env = _container_env_map()
    mounts = _container_mount_map()
    runtime_data_mount_path = mounts["runtime-data"]["mountPath"]

    assert env["NKVM_BACKUP_DIR"] == "/var/lib/nkvm/backups"
    assert env["NKVM_METADATA_DB_PATH"] == "/var/lib/nkvm/data/backups.db"
    assert env["NKVM_BACKUP_DIR"].startswith(runtime_data_mount_path)
    assert env["NKVM_METADATA_DB_PATH"].startswith(runtime_data_mount_path)


def test_deployment_with_remote_secret_mount_keeps_optional_and_read_only_semantics() -> None:
    mounts = _container_mount_map()
    volumes = _volume_map()
    remote_mount = mounts["remote-kubeconfig"]
    remote_secret = volumes["remote-kubeconfig"]["secret"]

    assert remote_mount["mountPath"] == "/etc/nkvm/remote"
    assert remote_mount["readOnly"] is True
    assert remote_secret["secretName"] == "nkvm-remote-kubeconfig"
    assert remote_secret["optional"] is True
    assert remote_secret["defaultMode"] == 0o400


def test_deployment_with_hardened_security_context_disables_privilege_escalation() -> None:
    deployment = _deployment_manifest()
    pod_security_context = deployment["spec"]["template"]["spec"]["securityContext"]
    container_security_context = _deployment_container()["securityContext"]

    assert pod_security_context["runAsNonRoot"] is True
    assert pod_security_context["seccompProfile"]["type"] == "RuntimeDefault"
    assert container_security_context["allowPrivilegeEscalation"] is False
    assert container_security_context["privileged"] is False
    assert container_security_context["capabilities"]["drop"] == ["ALL"]


def test_service_with_named_http_port_targets_container_http_port() -> None:
    service = _read_yaml_document("service.yaml")
    port = service["spec"]["ports"][0]

    assert port["name"] == "http"
    assert port["targetPort"] == "http"
    assert port["appProtocol"] == "http"


def test_pvc_with_runtime_storage_settings_uses_filesystem_and_rwonce() -> None:
    pvc = _read_yaml_document("persistentvolumeclaim.yaml")

    assert pvc["spec"]["accessModes"] == ["ReadWriteOnce"]
    assert pvc["spec"]["volumeMode"] == "Filesystem"
    assert pvc["spec"]["resources"]["requests"]["storage"] == "20Gi"


def test_kustomization_with_app_bundle_sets_expected_namespace_and_resources() -> None:
    kustomization = _read_yaml_document("kustomization.yaml")

    assert kustomization["namespace"] == "nerdy-k8s-volume-manager"
    assert kustomization["resources"] == [
        "namespace.yaml",
        "persistentvolumeclaim.yaml",
        "deployment.yaml",
        "service.yaml",
    ]
