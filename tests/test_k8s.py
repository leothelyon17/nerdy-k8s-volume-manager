from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from kubernetes.client import ApiException

from nerdy_k8s_volume_manager.k8s import (
    KubernetesClients,
    KubernetesAuthenticationError,
    KubernetesDiscoveryError,
    _resolve_controller_owner,
    _select_owner,
    get_cluster_summary,
    list_context_names,
    list_volume_records,
    load_kubernetes_clients,
    persist_kubeconfig_content,
)


def _owner_reference(*, kind: str, name: str, controller: bool = True) -> SimpleNamespace:
    return SimpleNamespace(kind=kind, name=name, controller=controller)


def _replica_set(*, owner_refs: list[SimpleNamespace] | None = None) -> SimpleNamespace:
    return SimpleNamespace(metadata=SimpleNamespace(owner_references=owner_refs or []))


def _job(*, owner_refs: list[SimpleNamespace] | None = None) -> SimpleNamespace:
    return SimpleNamespace(metadata=SimpleNamespace(owner_references=owner_refs or []))


def _pod(*, namespace: str, name: str, pvc_name: str, owner_refs: list[SimpleNamespace] | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        metadata=SimpleNamespace(namespace=namespace, name=name, owner_references=owner_refs or []),
        spec=SimpleNamespace(
            volumes=[
                SimpleNamespace(
                    persistent_volume_claim=SimpleNamespace(claim_name=pvc_name),
                )
            ]
        ),
    )


def _pvc(*, namespace: str, name: str, uid: str = "pvc-uid") -> SimpleNamespace:
    return SimpleNamespace(
        metadata=SimpleNamespace(namespace=namespace, name=name, uid=uid),
        spec=SimpleNamespace(
            access_modes=["ReadWriteOnce"],
            storage_class_name="fast",
            volume_name=f"pv-{name}",
        ),
        status=SimpleNamespace(phase="Bound", capacity={"storage": "1Gi"}),
    )


def _clients(*, core_api: Mock | None = None, apps_api: Mock | None = None, batch_api: Mock | None = None) -> KubernetesClients:
    return KubernetesClients(
        api_client=Mock(),
        core_api=core_api or Mock(),
        apps_api=apps_api or Mock(),
        batch_api=batch_api or Mock(),
    )


def test_resolve_controller_owner_with_replicaset_chain_returns_deployment() -> None:
    apps_api = Mock()
    apps_api.read_namespaced_replica_set.return_value = _replica_set(
        owner_refs=[_owner_reference(kind="Deployment", name="web", controller=True)]
    )
    clients = _clients(apps_api=apps_api)

    owner = _resolve_controller_owner(
        clients=clients,
        namespace="apps",
        kind="ReplicaSet",
        name="web-59cc4fd4f8",
        rs_cache={},
        job_cache={},
        depth=0,
    )

    assert owner == ("Deployment", "web")


def test_resolve_controller_owner_with_job_chain_returns_cronjob() -> None:
    batch_api = Mock()
    batch_api.read_namespaced_job.return_value = _job(
        owner_refs=[_owner_reference(kind="CronJob", name="nightly", controller=True)]
    )
    clients = _clients(batch_api=batch_api)

    owner = _resolve_controller_owner(
        clients=clients,
        namespace="ops",
        kind="Job",
        name="nightly-28977280",
        rs_cache={},
        job_cache={},
        depth=0,
    )

    assert owner == ("CronJob", "nightly")


def test_resolve_controller_owner_with_missing_replicaset_returns_unknown_owner_state() -> None:
    apps_api = Mock()
    apps_api.read_namespaced_replica_set.side_effect = ApiException(status=404, reason="Not Found")
    clients = _clients(apps_api=apps_api)

    owner = _resolve_controller_owner(
        clients=clients,
        namespace="apps",
        kind="ReplicaSet",
        name="missing-rs",
        rs_cache={},
        job_cache={},
        depth=0,
    )

    assert owner == ("Unknown", "ReplicaSet/missing-rs")


def test_resolve_controller_owner_with_replicaset_permission_error_raises_discovery_error() -> None:
    apps_api = Mock()
    apps_api.read_namespaced_replica_set.side_effect = ApiException(status=403, reason="Forbidden")
    clients = _clients(apps_api=apps_api)

    with pytest.raises(KubernetesDiscoveryError, match="ReplicaSet"):
        _resolve_controller_owner(
            clients=clients,
            namespace="apps",
            kind="ReplicaSet",
            name="web-rs",
            rs_cache={},
            job_cache={},
            depth=0,
        )


def test_select_owner_with_no_owners_returns_unknown_state() -> None:
    assert _select_owner([]) == ("Unknown", "Unknown")


def test_select_owner_with_multiple_distinct_owners_returns_deterministic_multiple_state() -> None:
    kind, name = _select_owner(
        [
            ("StatefulSet", "db"),
            ("Deployment", "api"),
            ("Deployment", "api"),
            ("DaemonSet", "agent"),
        ]
    )

    assert kind == "Multiple[DaemonSet,Deployment,StatefulSet]"
    assert name == "agent, api, db"


def test_list_volume_records_with_unconsumed_pvc_returns_unknown_owner_fields() -> None:
    core_api = Mock()
    core_api.list_namespaced_persistent_volume_claim.return_value = SimpleNamespace(items=[_pvc(namespace="apps", name="data")])
    core_api.list_namespaced_pod.return_value = SimpleNamespace(items=[])
    clients = _clients(core_api=core_api)

    records = list_volume_records(clients, namespaces=["apps"])

    assert len(records) == 1
    assert records[0].app_kind == "Unknown"
    assert records[0].app_name == "Unknown"


def test_list_volume_records_with_multiple_pod_consumers_returns_multiple_owner_state() -> None:
    core_api = Mock()
    core_api.list_namespaced_persistent_volume_claim.return_value = SimpleNamespace(
        items=[_pvc(namespace="apps", name="shared-data")]
    )
    core_api.list_namespaced_pod.return_value = SimpleNamespace(
        items=[
            _pod(
                namespace="apps",
                name="api-pod",
                pvc_name="shared-data",
                owner_refs=[_owner_reference(kind="ReplicaSet", name="api-rs")],
            ),
            _pod(
                namespace="apps",
                name="worker-pod",
                pvc_name="shared-data",
                owner_refs=[_owner_reference(kind="ReplicaSet", name="worker-rs")],
            ),
        ]
    )

    apps_api = Mock()
    owner_lookup = {"api-rs": "api", "worker-rs": "worker"}
    apps_api.read_namespaced_replica_set.side_effect = (
        lambda *, name, namespace: _replica_set(
            owner_refs=[_owner_reference(kind="Deployment", name=owner_lookup[name])]
        )
    )
    clients = _clients(core_api=core_api, apps_api=apps_api)

    records = list_volume_records(clients, namespaces=["apps"])

    assert len(records) == 1
    assert records[0].app_kind == "Multiple[Deployment]"
    assert records[0].app_name == "api, worker"


def test_list_volume_records_with_too_many_requested_namespaces_raises_discovery_error() -> None:
    clients = _clients()

    with pytest.raises(KubernetesDiscoveryError, match="exceeds the configured limit"):
        list_volume_records(
            clients,
            namespaces=["a", "b", "c"],
            max_namespace_scan=2,
        )


def test_list_volume_records_with_all_namespace_scan_over_limit_raises_discovery_error() -> None:
    core_api = Mock()
    core_api.list_namespace.return_value = SimpleNamespace(items=[object(), object(), object()])
    clients = _clients(core_api=core_api)

    with pytest.raises(KubernetesDiscoveryError, match="Cluster has 3 namespaces"):
        list_volume_records(
            clients,
            max_namespace_scan=2,
        )


def test_list_volume_records_with_api_exception_raises_actionable_discovery_error() -> None:
    core_api = Mock()
    core_api.list_namespaced_persistent_volume_claim.side_effect = ApiException(status=500, reason="boom")
    clients = _clients(core_api=core_api)

    with pytest.raises(KubernetesDiscoveryError, match="list PVCs in namespace 'apps'"):
        list_volume_records(
            clients,
            namespaces=["apps"],
        )


def test_get_cluster_summary_with_mocked_clients_returns_expected_counts() -> None:
    core_api = Mock()
    core_api.list_namespace.return_value = SimpleNamespace(items=[object(), object()])
    core_api.list_pod_for_all_namespaces.return_value = SimpleNamespace(items=[object()])
    core_api.list_persistent_volume_claim_for_all_namespaces.return_value = SimpleNamespace(items=[object(), object(), object()])
    clients = _clients(core_api=core_api)

    summary = get_cluster_summary(clients)

    assert summary == {
        "namespaces": 2,
        "pods": 1,
        "persistent_volume_claims": 3,
    }


def test_list_volume_records_with_all_namespace_scan_and_standalone_pod_owner() -> None:
    core_api = Mock()
    core_api.list_namespace.return_value = SimpleNamespace(items=[SimpleNamespace(metadata=SimpleNamespace(name="apps"))])
    core_api.list_persistent_volume_claim_for_all_namespaces.return_value = SimpleNamespace(
        items=[_pvc(namespace="apps", name="data")]
    )
    core_api.list_pod_for_all_namespaces.return_value = SimpleNamespace(
        items=[_pod(namespace="apps", name="standalone-pod", pvc_name="data", owner_refs=[])]
    )
    clients = _clients(core_api=core_api)

    records = list_volume_records(clients, request_timeout_seconds=7, max_namespace_scan=10)

    assert len(records) == 1
    assert records[0].app_kind == "Pod"
    assert records[0].app_name == "standalone-pod"
    core_api.list_namespace.assert_called_once_with(_request_timeout=7)


def test_list_volume_records_with_non_positive_guardrail_values_raises_value_error() -> None:
    clients = _clients()

    with pytest.raises(ValueError, match="request_timeout_seconds"):
        list_volume_records(clients, request_timeout_seconds=0)
    with pytest.raises(ValueError, match="max_namespace_scan"):
        list_volume_records(clients, max_namespace_scan=0)


def test_resolve_controller_owner_with_depth_limit_returns_unknown_state() -> None:
    clients = _clients()

    owner = _resolve_controller_owner(
        clients=clients,
        namespace="apps",
        kind="ReplicaSet",
        name="too-deep",
        rs_cache={},
        job_cache={},
        depth=5,
    )

    assert owner == ("Unknown", "ReplicaSet/too-deep")


def test_resolve_controller_owner_with_job_permission_error_raises_discovery_error() -> None:
    batch_api = Mock()
    batch_api.read_namespaced_job.side_effect = ApiException(status=500, reason="server-error")
    clients = _clients(batch_api=batch_api)

    with pytest.raises(KubernetesDiscoveryError, match="Job"):
        _resolve_controller_owner(
            clients=clients,
            namespace="ops",
            kind="Job",
            name="critical",
            rs_cache={},
            job_cache={},
            depth=0,
        )


def test_persist_kubeconfig_content_with_valid_yaml_writes_file_and_restricts_permissions() -> None:
    persisted_path = Path(persist_kubeconfig_content("apiVersion: v1\nkind: Config\n"))
    try:
        assert persisted_path.read_text(encoding="utf-8") == "apiVersion: v1\nkind: Config\n"
        assert persisted_path.stat().st_mode & 0o777 == 0o600
    finally:
        persisted_path.unlink(missing_ok=True)


def test_load_kubernetes_clients_with_in_cluster_mode_uses_incluster_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    load_incluster_config = Mock()
    load_kube_config = Mock()
    api_client = Mock()
    core_api = Mock()
    apps_api = Mock()
    batch_api = Mock()

    monkeypatch.setattr(
        "nerdy_k8s_volume_manager.k8s.config.load_incluster_config",
        load_incluster_config,
    )
    monkeypatch.setattr(
        "nerdy_k8s_volume_manager.k8s.config.load_kube_config",
        load_kube_config,
    )
    monkeypatch.setattr(
        "nerdy_k8s_volume_manager.k8s.client.ApiClient",
        Mock(return_value=api_client),
    )
    monkeypatch.setattr(
        "nerdy_k8s_volume_manager.k8s.client.CoreV1Api",
        Mock(return_value=core_api),
    )
    monkeypatch.setattr(
        "nerdy_k8s_volume_manager.k8s.client.AppsV1Api",
        Mock(return_value=apps_api),
    )
    monkeypatch.setattr(
        "nerdy_k8s_volume_manager.k8s.client.BatchV1Api",
        Mock(return_value=batch_api),
    )

    clients = load_kubernetes_clients(
        kubeconfig_path="~/.kube/config",
        context="ignored-context",
        in_cluster=True,
    )

    load_incluster_config.assert_called_once_with()
    load_kube_config.assert_not_called()
    assert clients.api_client is api_client
    assert clients.core_api is core_api
    assert clients.apps_api is apps_api
    assert clients.batch_api is batch_api


def test_load_kubernetes_clients_with_kubeconfig_mode_expands_path_and_context(monkeypatch: pytest.MonkeyPatch) -> None:
    load_incluster_config = Mock()
    load_kube_config = Mock()

    monkeypatch.setenv("HOME", "/tmp/nkvm-home")
    monkeypatch.setattr(
        "nerdy_k8s_volume_manager.k8s.config.load_incluster_config",
        load_incluster_config,
    )
    monkeypatch.setattr(
        "nerdy_k8s_volume_manager.k8s.config.load_kube_config",
        load_kube_config,
    )
    monkeypatch.setattr("nerdy_k8s_volume_manager.k8s.client.ApiClient", Mock(return_value=Mock()))
    monkeypatch.setattr("nerdy_k8s_volume_manager.k8s.client.CoreV1Api", Mock(return_value=Mock()))
    monkeypatch.setattr("nerdy_k8s_volume_manager.k8s.client.AppsV1Api", Mock(return_value=Mock()))
    monkeypatch.setattr("nerdy_k8s_volume_manager.k8s.client.BatchV1Api", Mock(return_value=Mock()))

    load_kubernetes_clients(
        kubeconfig_path="~/.kube/config",
        context="dev-cluster",
        in_cluster=False,
    )

    load_incluster_config.assert_not_called()
    load_kube_config.assert_called_once_with(
        config_file="/tmp/nkvm-home/.kube/config",
        context="dev-cluster",
    )


def test_load_kubernetes_clients_with_invalid_context_raises_authentication_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "nerdy_k8s_volume_manager.k8s.config.load_kube_config",
        Mock(side_effect=RuntimeError("context does not exist")),
    )

    with pytest.raises(KubernetesAuthenticationError, match="context does not exist"):
        load_kubernetes_clients(
            kubeconfig_path="/etc/nkvm/remote/config",
            context="missing-context",
            in_cluster=False,
        )


def test_list_context_names_with_mixed_contexts_returns_sorted_names(monkeypatch: pytest.MonkeyPatch) -> None:
    list_contexts = Mock(
        return_value=(
            [{"name": "zeta"}, {"name": "alpha"}, {"name": "delta"}],
            {"name": "delta"},
        )
    )
    monkeypatch.setenv("HOME", "/tmp/nkvm-home")
    monkeypatch.setattr(
        "nerdy_k8s_volume_manager.k8s.config.list_kube_config_contexts",
        list_contexts,
    )

    names = list_context_names("~/.kube/config")

    assert names == ["alpha", "delta", "zeta"]
    list_contexts.assert_called_once_with(config_file="/tmp/nkvm-home/.kube/config")


def test_list_context_names_with_invalid_kubeconfig_raises_authentication_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "nerdy_k8s_volume_manager.k8s.config.list_kube_config_contexts",
        Mock(side_effect=RuntimeError("parse failure")),
    )

    with pytest.raises(KubernetesAuthenticationError, match="Unable to list kubeconfig contexts"):
        list_context_names("/tmp/invalid-config")
