from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import tempfile
from typing import Callable, Iterable, TypeVar

from kubernetes import client, config
from kubernetes.client import ApiException

from .models import VolumeRecord

UNKNOWN_OWNER_KIND = "Unknown"
UNKNOWN_OWNER_NAME = "Unknown"
DEFAULT_DISCOVERY_TIMEOUT_SECONDS = 20
DEFAULT_MAX_NAMESPACE_SCAN = 100
MAX_OWNER_RESOLUTION_DEPTH = 5
T = TypeVar("T")


@dataclass(frozen=True)
class KubernetesClients:
    api_client: client.ApiClient
    core_api: client.CoreV1Api
    apps_api: client.AppsV1Api
    batch_api: client.BatchV1Api


class KubernetesDiscoveryError(RuntimeError):
    """Raised when PVC discovery cannot safely continue."""


class KubernetesAuthenticationError(RuntimeError):
    """Raised when Kubernetes authentication configuration fails."""


def persist_kubeconfig_content(kubeconfig_content: str) -> str:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as handle:
        handle.write(kubeconfig_content)
        path = Path(handle.name)
    os.chmod(path, 0o600)
    return str(path)


def load_kubernetes_clients(
    *,
    kubeconfig_path: str | None,
    context: str | None,
    in_cluster: bool,
) -> KubernetesClients:
    expanded = _expand_kubeconfig_path(kubeconfig_path)
    try:
        if in_cluster:
            config.load_incluster_config()
        else:
            config.load_kube_config(config_file=expanded, context=context)
    except Exception as error:  # pylint: disable=broad-except
        raise KubernetesAuthenticationError(
            _format_authentication_error(
                in_cluster=in_cluster,
                kubeconfig_path=expanded,
                context=context,
                error=error,
            )
        ) from error

    api_client = client.ApiClient()
    return KubernetesClients(
        api_client=api_client,
        core_api=client.CoreV1Api(api_client),
        apps_api=client.AppsV1Api(api_client),
        batch_api=client.BatchV1Api(api_client),
    )


def list_context_names(kubeconfig_path: str | None = None) -> list[str]:
    expanded = _expand_kubeconfig_path(kubeconfig_path)
    try:
        contexts, _ = config.list_kube_config_contexts(config_file=expanded)
    except Exception as error:  # pylint: disable=broad-except
        reason = str(error).strip() or error.__class__.__name__
        source = expanded or "default kubeconfig search path"
        raise KubernetesAuthenticationError(
            f"Unable to list kubeconfig contexts from '{source}': {reason}. "
            "Verify the kubeconfig path is readable and valid."
        ) from error
    if not contexts:
        return []
    return sorted(context["name"] for context in contexts)


def get_cluster_summary(clients: KubernetesClients) -> dict[str, int]:
    namespaces_count = len(clients.core_api.list_namespace().items)
    pods_count = len(clients.core_api.list_pod_for_all_namespaces().items)
    pvc_count = len(clients.core_api.list_persistent_volume_claim_for_all_namespaces().items)
    return {
        "namespaces": namespaces_count,
        "pods": pods_count,
        "persistent_volume_claims": pvc_count,
    }


def list_volume_records(
    clients: KubernetesClients,
    *,
    namespaces: Iterable[str] | None = None,
    last_success_map: dict[tuple[str, str], str] | None = None,
    request_timeout_seconds: int = DEFAULT_DISCOVERY_TIMEOUT_SECONDS,
    max_namespace_scan: int = DEFAULT_MAX_NAMESPACE_SCAN,
) -> list[VolumeRecord]:
    if request_timeout_seconds <= 0:
        raise ValueError("request_timeout_seconds must be positive")
    if max_namespace_scan <= 0:
        raise ValueError("max_namespace_scan must be positive")

    target_namespaces = sorted({namespace.strip() for namespace in namespaces or [] if namespace and namespace.strip()})
    if target_namespaces:
        if len(target_namespaces) > max_namespace_scan:
            raise KubernetesDiscoveryError(
                f"Requested scan for {len(target_namespaces)} namespaces, which exceeds the configured limit "
                f"({max_namespace_scan}). Reduce the namespace filter size or increase the limit."
            )

        pvc_items: list[client.V1PersistentVolumeClaim] = []
        pod_items: list[client.V1Pod] = []
        for namespace in target_namespaces:
            pvc_items.extend(
                _safe_kubernetes_discovery_call(
                    operation=f"list PVCs in namespace '{namespace}'",
                    hint=(
                        "Check namespace spelling, API reachability, and RBAC verbs for "
                        "persistentvolumeclaims."
                    ),
                    func=lambda namespace=namespace: clients.core_api.list_namespaced_persistent_volume_claim(
                        namespace=namespace,
                        _request_timeout=request_timeout_seconds,
                    ).items,
                )
            )
            pod_items.extend(
                _safe_kubernetes_discovery_call(
                    operation=f"list Pods in namespace '{namespace}'",
                    hint="Check RBAC verbs for pods and confirm the namespace still exists.",
                    func=lambda namespace=namespace: clients.core_api.list_namespaced_pod(
                        namespace=namespace,
                        _request_timeout=request_timeout_seconds,
                    ).items,
                )
            )
    else:
        namespace_count = len(
            _safe_kubernetes_discovery_call(
                operation="list namespaces for discovery preflight",
                hint="Confirm cluster connectivity and RBAC verbs for namespaces.",
                func=lambda: clients.core_api.list_namespace(_request_timeout=request_timeout_seconds).items,
            )
        )
        if namespace_count > max_namespace_scan:
            raise KubernetesDiscoveryError(
                f"Cluster has {namespace_count} namespaces, exceeding the configured discovery limit "
                f"({max_namespace_scan}). Apply a namespace filter or increase the limit."
            )

        pvc_items = _safe_kubernetes_discovery_call(
            operation="list PVCs across all namespaces",
            hint=(
                "Apply a namespace filter for large clusters or verify RBAC verbs for "
                "persistentvolumeclaims."
            ),
            func=lambda: clients.core_api.list_persistent_volume_claim_for_all_namespaces(
                _request_timeout=request_timeout_seconds
            ).items,
        )
        pod_items = _safe_kubernetes_discovery_call(
            operation="list Pods across all namespaces",
            hint="Apply a namespace filter for large clusters or verify RBAC verbs for pods.",
            func=lambda: clients.core_api.list_pod_for_all_namespaces(_request_timeout=request_timeout_seconds).items,
        )

    consumer_index = _build_pvc_consumer_index(clients, pod_items)
    last_success_map = last_success_map or {}

    records: list[VolumeRecord] = []
    for pvc in pvc_items:
        namespace = pvc.metadata.namespace or ""
        pvc_name = pvc.metadata.name or ""
        pvc_uid = pvc.metadata.uid or ""

        owners = consumer_index.get((namespace, pvc_name), [])
        app_kind, app_name = _select_owner(owners)

        capacity = None
        if pvc.status and pvc.status.capacity:
            capacity = pvc.status.capacity.get("storage")

        access_modes = tuple(pvc.spec.access_modes or ()) if pvc.spec else ()
        phase = pvc.status.phase if pvc.status and pvc.status.phase else "Unknown"
        storage_class = pvc.spec.storage_class_name if pvc.spec else None
        bound_pv = pvc.spec.volume_name if pvc.spec else None

        records.append(
            VolumeRecord(
                namespace=namespace,
                pvc_name=pvc_name,
                pvc_uid=pvc_uid,
                phase=phase,
                capacity=capacity,
                storage_class=storage_class,
                access_modes=access_modes,
                bound_pv=bound_pv,
                app_kind=app_kind,
                app_name=app_name,
                last_successful_backup_at=last_success_map.get((namespace, pvc_name)),
            )
        )

    records.sort(key=lambda item: (item.namespace, item.pvc_name))
    return records


def _select_owner(owners: list[tuple[str, str]]) -> tuple[str | None, str | None]:
    if not owners:
        return UNKNOWN_OWNER_KIND, UNKNOWN_OWNER_NAME

    normalized_owners = sorted(
        {
            (
                kind or UNKNOWN_OWNER_KIND,
                name or UNKNOWN_OWNER_NAME,
            )
            for kind, name in owners
        }
    )
    if len(normalized_owners) == 1:
        return normalized_owners[0]

    kinds = sorted({kind for kind, _ in normalized_owners})
    unique_names = sorted({name for _, name in normalized_owners})
    names = ", ".join(unique_names[:3])
    if len(unique_names) > 3:
        names = f"{names}, ..."
    return f"Multiple[{','.join(kinds)}]", names


def _build_pvc_consumer_index(
    clients: KubernetesClients,
    pods: list[client.V1Pod],
) -> dict[tuple[str, str], list[tuple[str, str]]]:
    index: dict[tuple[str, str], list[tuple[str, str]]] = {}
    rs_cache: dict[tuple[str, str], client.V1ReplicaSet | None] = {}
    job_cache: dict[tuple[str, str], client.V1Job | None] = {}

    for pod in pods:
        namespace = pod.metadata.namespace if pod.metadata else None
        if not namespace:
            continue

        owner = _resolve_pod_owner(clients, pod, namespace, rs_cache, job_cache)

        for volume in pod.spec.volumes or []:
            pvc_source = volume.persistent_volume_claim
            if not pvc_source or not pvc_source.claim_name:
                continue

            key = (namespace, pvc_source.claim_name)
            index.setdefault(key, [])
            if owner not in index[key]:
                index[key].append(owner)

    return index


def _resolve_pod_owner(
    clients: KubernetesClients,
    pod: client.V1Pod,
    namespace: str,
    rs_cache: dict[tuple[str, str], client.V1ReplicaSet | None],
    job_cache: dict[tuple[str, str], client.V1Job | None],
) -> tuple[str, str]:
    refs = pod.metadata.owner_references if pod.metadata and pod.metadata.owner_references else []
    if not refs:
        return "Pod", pod.metadata.name if pod.metadata and pod.metadata.name else UNKNOWN_OWNER_NAME

    owner_ref = _controller_reference(refs)
    if owner_ref is None:
        return "Pod", pod.metadata.name if pod.metadata and pod.metadata.name else UNKNOWN_OWNER_NAME

    return _resolve_controller_owner(
        clients=clients,
        namespace=namespace,
        kind=owner_ref.kind or UNKNOWN_OWNER_KIND,
        name=owner_ref.name or UNKNOWN_OWNER_NAME,
        rs_cache=rs_cache,
        job_cache=job_cache,
        depth=0,
    )


def _resolve_controller_owner(
    *,
    clients: KubernetesClients,
    namespace: str,
    kind: str,
    name: str,
    rs_cache: dict[tuple[str, str], client.V1ReplicaSet | None],
    job_cache: dict[tuple[str, str], client.V1Job | None],
    depth: int,
) -> tuple[str, str]:
    if depth >= MAX_OWNER_RESOLUTION_DEPTH:
        return UNKNOWN_OWNER_KIND, f"{kind}/{name}"

    if kind == "ReplicaSet":
        rs = _read_replicaset(clients, namespace, name, rs_cache)
        if rs is None:
            return UNKNOWN_OWNER_KIND, f"ReplicaSet/{name}"
        if rs.metadata and rs.metadata.owner_references:
            ref = _controller_reference(rs.metadata.owner_references)
            if ref is not None:
                return _resolve_controller_owner(
                    clients=clients,
                    namespace=namespace,
                    kind=ref.kind or UNKNOWN_OWNER_KIND,
                    name=ref.name or UNKNOWN_OWNER_NAME,
                    rs_cache=rs_cache,
                    job_cache=job_cache,
                    depth=depth + 1,
                )

    if kind == "Job":
        job = _read_job(clients, namespace, name, job_cache)
        if job is None:
            return UNKNOWN_OWNER_KIND, f"Job/{name}"
        if job.metadata and job.metadata.owner_references:
            ref = _controller_reference(job.metadata.owner_references)
            if ref is not None:
                return _resolve_controller_owner(
                    clients=clients,
                    namespace=namespace,
                    kind=ref.kind or UNKNOWN_OWNER_KIND,
                    name=ref.name or UNKNOWN_OWNER_NAME,
                    rs_cache=rs_cache,
                    job_cache=job_cache,
                    depth=depth + 1,
                )

    return kind, name


def _read_replicaset(
    clients: KubernetesClients,
    namespace: str,
    name: str,
    cache: dict[tuple[str, str], client.V1ReplicaSet | None],
) -> client.V1ReplicaSet | None:
    key = (namespace, name)
    if key in cache:
        return cache[key]

    try:
        cache[key] = clients.apps_api.read_namespaced_replica_set(name=name, namespace=namespace)
    except ApiException as error:
        if error.status == 404:
            cache[key] = None
        else:
            raise KubernetesDiscoveryError(
                _format_api_exception_message(
                    operation=f"resolve ReplicaSet owner '{namespace}/{name}'",
                    hint="Verify RBAC allows get on ReplicaSets and retry discovery.",
                    error=error,
                )
            ) from error
    return cache[key]


def _read_job(
    clients: KubernetesClients,
    namespace: str,
    name: str,
    cache: dict[tuple[str, str], client.V1Job | None],
) -> client.V1Job | None:
    key = (namespace, name)
    if key in cache:
        return cache[key]

    try:
        cache[key] = clients.batch_api.read_namespaced_job(name=name, namespace=namespace)
    except ApiException as error:
        if error.status == 404:
            cache[key] = None
        else:
            raise KubernetesDiscoveryError(
                _format_api_exception_message(
                    operation=f"resolve Job owner '{namespace}/{name}'",
                    hint="Verify RBAC allows get on Jobs and retry discovery.",
                    error=error,
                )
            ) from error
    return cache[key]


def _safe_kubernetes_discovery_call(*, operation: str, hint: str, func: Callable[[], T]) -> T:
    try:
        return func()
    except ApiException as error:
        raise KubernetesDiscoveryError(
            _format_api_exception_message(
                operation=operation,
                hint=hint,
                error=error,
            )
        ) from error
    except Exception as error:
        raise KubernetesDiscoveryError(
            f"Kubernetes discovery failed while trying to {operation}: {error}. {hint}"
        ) from error


def _format_api_exception_message(*, operation: str, hint: str, error: ApiException) -> str:
    status = error.status if error.status is not None else "unknown"
    reason = error.reason or "no reason provided"
    return (
        f"Kubernetes discovery failed while trying to {operation}: "
        f"API status {status} ({reason}). {hint}"
    )


def _controller_reference(owner_refs: list[client.V1OwnerReference]) -> client.V1OwnerReference | None:
    for owner_ref in owner_refs:
        if owner_ref.controller:
            return owner_ref
    return owner_refs[0] if owner_refs else None


def _expand_kubeconfig_path(kubeconfig_path: str | None) -> str | None:
    if kubeconfig_path is None:
        return None
    stripped = kubeconfig_path.strip()
    if not stripped:
        return None
    return str(Path(stripped).expanduser())


def _format_authentication_error(
    *,
    in_cluster: bool,
    kubeconfig_path: str | None,
    context: str | None,
    error: Exception,
) -> str:
    reason = str(error).strip() or error.__class__.__name__
    if in_cluster:
        return (
            "Kubernetes authentication setup failed while loading in-cluster service account credentials: "
            f"{reason}. Ensure the pod has a mounted service account token and Kubernetes service host "
            "environment variables."
        )

    kubeconfig_source = kubeconfig_path or "default kubeconfig search path"
    context_message = f" with context '{context}'" if context else ""
    return (
        "Kubernetes authentication setup failed while loading kubeconfig "
        f"from '{kubeconfig_source}'{context_message}: {reason}. "
        "Verify the kubeconfig path and context are valid."
    )
