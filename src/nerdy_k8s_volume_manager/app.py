from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import os

import streamlit as st
import yaml

from nerdy_k8s_volume_manager.backup import BackupManager, BackupManagerConfig, RemoteDestinationConfig
from nerdy_k8s_volume_manager.config import AppConfig, ensure_directories
from nerdy_k8s_volume_manager.k8s import (
    KubernetesDiscoveryError,
    get_cluster_summary,
    list_volume_records,
    load_kubernetes_clients,
    persist_kubeconfig_content,
)
from nerdy_k8s_volume_manager.metadata import BackupMetadataStore
from nerdy_k8s_volume_manager.models import BackupResult, VolumeRecord

_AUTH_MODE_USE_KUBECONFIG_PATH = "Use kubeconfig path"
_AUTH_MODE_PASTE_KUBECONFIG = "Paste kubeconfig"
_AUTH_MODE_IN_CLUSTER = "In-cluster service account"

_DESTINATION_LOCAL_LABEL = "Local pod/container volume"
_DESTINATION_REMOTE_LABEL = "Remote destination"
_REMOTE_PROTOCOL_FTP_LABEL = "FTP"
_REMOTE_PROTOCOL_FTPS_LABEL = "FTPS"
_REMOTE_PROTOCOL_SCP_LABEL = "SCP"
_REMOTE_PROTOCOL_RSYNC_LABEL = "RSYNC"

_BATCH_MODE_SEQUENTIAL_LABEL = "Sequential (available)"
_BATCH_MODE_PARALLEL_LABEL = "Parallel (planned)"

_WORKFLOW_STATE_LABELS = {
    "done": "Done",
    "active": "Ready",
    "blocked": "Waiting",
}

_STAGE_HINTS: tuple[tuple[str, str], ...] = (
    (
        "create stage failed",
        "Verify RBAC allows helper pod creation and the helper image can be pulled.",
    ),
    (
        "wait stage failed",
        "Inspect pod events and consider increasing helper pod timeout for slow nodes.",
    ),
    (
        "exec stage failed",
        "Confirm the helper image has shell and tar available and PVC mount is readable.",
    ),
    (
        "copy stage failed",
        "Check local kubectl availability and ensure kubeconfig/context points to this cluster.",
    ),
    (
        "remote stage failed",
        "Validate remote protocol, host, credentials, and directory permissions.",
    ),
    (
        "checksum stage failed",
        "Validate the local backup directory is writable and archive generation completed.",
    ),
    (
        "cleanup stage failed",
        "Review permissions to delete helper pods in the namespace after backup.",
    ),
    (
        "unexpected backup failure",
        "Inspect Kubernetes events and application logs for this PVC backup attempt.",
    ),
)


@dataclass(frozen=True)
class BatchExecutionSettings:
    mode: str
    requested_max_workers: int
    effective_max_workers: int
    stop_on_failure: bool


def _initialize_state() -> None:
    defaults = {
        "connected": False,
        "connection": {},
        "clients": None,
        "volume_records": [],
        "last_backup_results": [],
        "selected_volume_labels": [],
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def _build_volume_rows(volumes: list[VolumeRecord]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for volume in volumes:
        rows.append(
            {
                "namespace": volume.namespace,
                "pvc": volume.pvc_name,
                "app": f"{volume.app_kind or 'Unknown'}/{volume.app_name or 'Unknown'}",
                "phase": volume.phase,
                "capacity": volume.capacity or "unknown",
                "storage_class": volume.storage_class or "unknown",
                "access_modes": ",".join(volume.access_modes),
                "bound_pv": volume.bound_pv or "unknown",
                "last_successful_backup_at": volume.last_successful_backup_at or "never",
            }
        )
    return rows


def _actionable_next_step(message: str) -> str:
    normalized = message.strip()
    if not normalized:
        return "No follow-up action required."

    for stage, hint in _STAGE_HINTS:
        if stage in normalized:
            return f"{normalized} | Next step: {hint}"
    return f"{normalized} | Next step: Inspect pod events and application logs for more detail."


def _build_result_rows(results: list[BackupResult]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for result in results:
        actionable_message = "Backup completed successfully."
        if result.status != "success":
            actionable_message = _actionable_next_step(result.message)

        rows.append(
            {
                "namespace": result.namespace,
                "pvc": result.pvc_name,
                "status": result.status,
                "backup_path": result.backup_path or "",
                "checksum_sha256": result.checksum_sha256 or "",
                "finished_at": result.finished_at,
                "message": result.message,
                "actionable_message": actionable_message,
            }
        )
    return rows


def _build_history_rows(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    rendered_rows: list[dict[str, str]] = []
    for row in rows:
        status = str(row.get("status", ""))
        message = str(row.get("message", "") or "")
        actionable_message = "Backup completed successfully."
        if status != "success":
            actionable_message = _actionable_next_step(message)

        rendered_rows.append(
            {
                "namespace": str(row.get("namespace", "")),
                "pvc_name": str(row.get("pvc_name", "")),
                "status": status,
                "backup_path": str(row.get("backup_path", "") or ""),
                "checksum_sha256": str(row.get("checksum_sha256", "") or ""),
                "created_at": str(row.get("created_at", "")),
                "message": message,
                "actionable_message": actionable_message,
            }
        )
    return rendered_rows


def _build_batch_execution_settings(
    *,
    mode_label: str,
    requested_max_workers: int,
    stop_on_failure: bool,
) -> BatchExecutionSettings:
    normalized_workers = max(1, requested_max_workers)
    mode = "sequential"
    if mode_label == _BATCH_MODE_PARALLEL_LABEL:
        mode = "parallel_preview"

    return BatchExecutionSettings(
        mode=mode,
        requested_max_workers=normalized_workers,
        effective_max_workers=1,
        stop_on_failure=stop_on_failure,
    )


def _build_workflow_rows(
    *,
    connected: bool,
    discovered_count: int,
    selected_count: int,
    backup_results_count: int,
) -> list[dict[str, str]]:
    connect_state = "done" if connected else "active"
    discover_state = "done" if discovered_count > 0 else ("active" if connected else "blocked")
    select_state = "done" if selected_count > 0 else ("active" if discovered_count > 0 else "blocked")
    backup_state = "done" if backup_results_count > 0 else ("active" if selected_count > 0 else "blocked")
    review_state = "done" if backup_results_count > 0 else ("active" if connected else "blocked")

    return [
        {
            "step": "1. Connect",
            "state": _WORKFLOW_STATE_LABELS[connect_state],
            "description": "Authenticate to the cluster from the sidebar.",
        },
        {
            "step": "2. Discover",
            "state": _WORKFLOW_STATE_LABELS[discover_state],
            "description": "Refresh PVC inventory and owner mappings.",
        },
        {
            "step": "3. Select",
            "state": _WORKFLOW_STATE_LABELS[select_state],
            "description": "Choose one or more PVCs for backup.",
        },
        {
            "step": "4. Backup",
            "state": _WORKFLOW_STATE_LABELS[backup_state],
            "description": "Run batch backup and capture success or failure details.",
        },
        {
            "step": "5. Review",
            "state": _WORKFLOW_STATE_LABELS[review_state],
            "description": "Inspect the latest run and recent backup history.",
        },
    ]


def _validate_connection_inputs(*, auth_mode: str, kubeconfig_path_input: str, kubeconfig_text_input: str) -> str | None:
    if auth_mode == _AUTH_MODE_USE_KUBECONFIG_PATH:
        return _validate_kubeconfig_path_input(kubeconfig_path_input)

    if auth_mode == _AUTH_MODE_PASTE_KUBECONFIG:
        kubeconfig_text = kubeconfig_text_input.strip()
        if not kubeconfig_text:
            return "Paste kubeconfig content before connecting."
        return _validate_kubeconfig_content(
            kubeconfig_content=kubeconfig_text,
            source_label="Pasted kubeconfig",
        )

    if auth_mode == _AUTH_MODE_IN_CLUSTER and not _is_incluster_service_account_environment():
        return (
            "In-cluster service account mode requires Kubernetes pod environment variables and the "
            "service-account token mount."
        )

    return None


def _validate_runtime_paths(*, backup_dir_input: str, metadata_db_path_input: str) -> list[str]:
    errors: list[str] = []
    if not backup_dir_input.strip():
        errors.append("Backup directory path is required.")
    if not metadata_db_path_input.strip():
        errors.append("Metadata DB path is required.")
    return errors


def _validate_remote_destination_inputs(
    *,
    host_input: str,
    username_input: str,
    password_input: str,
    directory_input: str,
) -> list[str]:
    errors: list[str] = []
    if not host_input.strip():
        errors.append("Remote destination IP/hostname is required.")
    if not username_input.strip():
        errors.append("Remote destination username is required.")
    if not password_input:
        errors.append("Remote destination password is required.")
    if not directory_input.strip():
        errors.append("Remote destination directory is required.")
    return errors


def _label_for_volume(volume: VolumeRecord) -> str:
    return (
        f"{volume.namespace}/{volume.pvc_name}"
        f" | app={volume.app_kind or 'Unknown'}/{volume.app_name or 'Unknown'}"
        f" | last={volume.last_successful_backup_at or 'never'}"
    )


def _remote_protocol_value(protocol_label: str) -> str:
    if protocol_label == _REMOTE_PROTOCOL_FTPS_LABEL:
        return "ftps"
    if protocol_label == _REMOTE_PROTOCOL_SCP_LABEL:
        return "scp"
    if protocol_label == _REMOTE_PROTOCOL_RSYNC_LABEL:
        return "rsync"
    return "ftp"


def _default_auth_mode() -> str:
    configured_default = os.getenv("NKVM_DEFAULT_AUTH_MODE", "").strip().lower()
    if configured_default in {"kubeconfig", "kubeconfig_path", "path"}:
        return _AUTH_MODE_USE_KUBECONFIG_PATH
    if configured_default in {"paste", "pasted", "kubeconfig_text"}:
        return _AUTH_MODE_PASTE_KUBECONFIG
    if configured_default in {"in-cluster", "in_cluster", "serviceaccount", "service-account"}:
        return _AUTH_MODE_IN_CLUSTER

    if _is_incluster_service_account_environment():
        return _AUTH_MODE_IN_CLUSTER

    return _AUTH_MODE_USE_KUBECONFIG_PATH


def _is_incluster_service_account_environment() -> bool:
    return bool(
        os.getenv("KUBERNETES_SERVICE_HOST")
        and Path("/var/run/secrets/kubernetes.io/serviceaccount/token").exists()
    )


def _auth_mode_guidance(auth_mode: str) -> str:
    if auth_mode == _AUTH_MODE_IN_CLUSTER:
        return (
            "Primary mode for in-cluster deployments. Uses ServiceAccount credentials from the running pod "
            "(no kubeconfig file path required)."
        )
    if auth_mode == _AUTH_MODE_USE_KUBECONFIG_PATH:
        return (
            "Use for local runs or remote cluster targets. Provide a readable kubeconfig file path "
            "(for in-cluster remote targeting, mount a Secret such as /etc/nkvm/remote/config)."
        )
    return (
        "Use only for short-lived troubleshooting. Paste a full kubeconfig with apiVersion, clusters, "
        "contexts, and users."
    )


def _validate_kubeconfig_path_input(kubeconfig_path_input: str) -> str | None:
    path_value = kubeconfig_path_input.strip()
    if not path_value:
        return "Kubeconfig path is required when using kubeconfig path authentication."

    expanded_path = Path(path_value).expanduser()
    if not expanded_path.exists():
        return f"Kubeconfig path does not exist: {expanded_path}"
    if not expanded_path.is_file():
        return f"Kubeconfig path must point to a file: {expanded_path}"

    try:
        kubeconfig_content = expanded_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return f"Kubeconfig path must reference a UTF-8 text file: {expanded_path}"
    except OSError as error:
        return f"Unable to read kubeconfig path {expanded_path}: {error}"

    return _validate_kubeconfig_content(
        kubeconfig_content=kubeconfig_content,
        source_label=f"Kubeconfig file '{expanded_path}'",
    )


def _validate_kubeconfig_content(*, kubeconfig_content: str, source_label: str) -> str | None:
    try:
        parsed = yaml.safe_load(kubeconfig_content)
    except yaml.YAMLError as error:
        return f"{source_label} must be valid YAML: {error.__class__.__name__}."

    if not isinstance(parsed, dict):
        return f"{source_label} must be a YAML mapping."

    required_fields = ("apiVersion", "clusters", "contexts", "users")
    missing_fields = [field for field in required_fields if field not in parsed]
    if missing_fields:
        missing_fields_csv = ", ".join(missing_fields)
        return f"{source_label} is missing required field(s): {missing_fields_csv}."

    for list_field in ("clusters", "contexts", "users"):
        values = parsed.get(list_field)
        if not isinstance(values, list) or not values:
            return f"{source_label} must include at least one '{list_field}' entry."

    return None


def _run_batch_backup(
    *,
    manager: BackupManager,
    metadata_store: BackupMetadataStore,
    selected_volumes: list[VolumeRecord],
    settings: BatchExecutionSettings,
) -> list[BackupResult]:
    total = len(selected_volumes)
    progress = st.progress(0.0, text=f"Queued {total} volume(s) for backup.")

    results: list[BackupResult] = []
    for index, volume in enumerate(selected_volumes, start=1):
        progress.progress(
            (index - 1) / total,
            text=f"[{index}/{total}] Backing up {volume.namespace}/{volume.pvc_name}...",
        )
        result = manager.backup_one(volume)
        metadata_store.record_result(result)
        results.append(result)

        progress.progress(
            index / total,
            text=f"[{index}/{total}] Finished {volume.namespace}/{volume.pvc_name} ({result.status}).",
        )
        if settings.stop_on_failure and result.status != "success":
            break

    return results


def main() -> None:
    st.set_page_config(page_title="Nerdy K8s Volume Manager", layout="wide")
    _initialize_state()

    base_config = AppConfig()
    ensure_directories(base_config)

    st.title("Nerdy K8s Volume Manager")
    st.caption("Discover PVC ownership, run on-demand tar backups, and track backup history.")
    st.subheader("Workflow Status")
    st.dataframe(
        _build_workflow_rows(
            connected=bool(st.session_state.connected and st.session_state.clients is not None),
            discovered_count=len(st.session_state.volume_records),
            selected_count=len(st.session_state.selected_volume_labels),
            backup_results_count=len(st.session_state.last_backup_results),
        ),
        use_container_width=True,
        hide_index=True,
    )

    st.sidebar.header("Cluster Connection")
    auth_options = [_AUTH_MODE_USE_KUBECONFIG_PATH, _AUTH_MODE_PASTE_KUBECONFIG, _AUTH_MODE_IN_CLUSTER]
    default_auth_mode = _default_auth_mode()
    auth_mode = st.sidebar.radio(
        "Authentication",
        options=auth_options,
        index=auth_options.index(default_auth_mode),
    )
    st.sidebar.caption(_auth_mode_guidance(auth_mode))
    context = st.sidebar.text_input(
        "Kubernetes context (optional)",
        value="",
        help=(
            "Ignored for in-cluster service account mode."
            if auth_mode == _AUTH_MODE_IN_CLUSTER
            else "Optional kubeconfig context override."
        ),
    )

    kubeconfig_path_input = "~/.kube/config"
    kubeconfig_text_input = ""
    if auth_mode == _AUTH_MODE_USE_KUBECONFIG_PATH:
        kubeconfig_path_input = st.sidebar.text_input("Kubeconfig path", value="~/.kube/config")
    elif auth_mode == _AUTH_MODE_PASTE_KUBECONFIG:
        kubeconfig_text_input = st.sidebar.text_area("Kubeconfig content", height=220)

    st.sidebar.header("Backup Destination")
    destination_label = st.sidebar.selectbox(
        "Destination type",
        options=[_DESTINATION_LOCAL_LABEL, _DESTINATION_REMOTE_LABEL],
        index=0,
    )
    backup_dir_input = str(base_config.backup_dir)
    remote_protocol_label = _REMOTE_PROTOCOL_FTP_LABEL
    remote_host_input = ""
    remote_username_input = ""
    remote_password_input = ""
    remote_directory_input = "/"

    if destination_label == _DESTINATION_LOCAL_LABEL:
        backup_dir_input = st.sidebar.text_input("Backup directory", value=str(base_config.backup_dir))
        st.sidebar.caption("Backups remain on the app runtime volume.")
    else:
        remote_protocol_label = st.sidebar.selectbox(
            "Remote protocol",
            options=[
                _REMOTE_PROTOCOL_FTP_LABEL,
                _REMOTE_PROTOCOL_FTPS_LABEL,
                _REMOTE_PROTOCOL_SCP_LABEL,
                _REMOTE_PROTOCOL_RSYNC_LABEL,
            ],
            index=0,
        )
        remote_host_input = st.sidebar.text_input("Remote IP/Hostname", value="")
        remote_username_input = st.sidebar.text_input("Remote username", value="")
        remote_password_input = st.sidebar.text_input("Remote password", value="", type="password")
        remote_directory_input = st.sidebar.text_input("Remote backup directory", value="/")
        st.sidebar.caption(f"Local staging directory: {base_config.backup_dir}")

    metadata_db_path_input = str(base_config.metadata_db_path)
    st.sidebar.caption(f"Metadata DB path (always local): {metadata_db_path_input}")

    st.sidebar.header("Batch Execution")
    mode_label = st.sidebar.selectbox(
        "Execution mode",
        options=[_BATCH_MODE_SEQUENTIAL_LABEL, _BATCH_MODE_PARALLEL_LABEL],
        index=0,
        help="Parallel mode is planned and currently executes sequentially with preview settings.",
    )
    requested_max_workers = int(
        st.sidebar.number_input(
            "Max parallel workers (preview)",
            min_value=1,
            max_value=32,
            value=4,
            step=1,
            disabled=mode_label == _BATCH_MODE_SEQUENTIAL_LABEL,
        )
    )
    stop_on_failure = st.sidebar.checkbox(
        "Stop batch on first failure",
        value=False,
        help="If enabled, the current run stops after the first failed volume.",
    )
    batch_settings = _build_batch_execution_settings(
        mode_label=mode_label,
        requested_max_workers=requested_max_workers,
        stop_on_failure=stop_on_failure,
    )
    st.sidebar.caption(
        "Effective execution: "
        f"{batch_settings.mode} "
        f"(effective_workers={batch_settings.effective_max_workers}, "
        f"requested_workers={batch_settings.requested_max_workers})"
    )

    if st.sidebar.button("Connect", type="primary"):
        connection_error = _validate_connection_inputs(
            auth_mode=auth_mode,
            kubeconfig_path_input=kubeconfig_path_input,
            kubeconfig_text_input=kubeconfig_text_input,
        )
        if connection_error:
            st.sidebar.error(connection_error)
        else:
            try:
                kubeconfig_path: str | None = None
                in_cluster = auth_mode == _AUTH_MODE_IN_CLUSTER

                if auth_mode == _AUTH_MODE_USE_KUBECONFIG_PATH:
                    kubeconfig_path = str(Path(kubeconfig_path_input).expanduser())
                elif auth_mode == _AUTH_MODE_PASTE_KUBECONFIG:
                    kubeconfig_path = persist_kubeconfig_content(kubeconfig_text_input)

                clients = load_kubernetes_clients(
                    kubeconfig_path=kubeconfig_path,
                    context=context or None,
                    in_cluster=in_cluster,
                )

                st.session_state.connected = True
                st.session_state.clients = clients
                st.session_state.connection = {
                    "auth_mode": auth_mode,
                    "kubeconfig_path": kubeconfig_path,
                    "context": context or None,
                    "in_cluster": in_cluster,
                }
                st.session_state.volume_records = []
                st.session_state.last_backup_results = []
                st.session_state.selected_volume_labels = []
                st.success("Connected to Kubernetes cluster.")
            except Exception as error:  # pylint: disable=broad-except
                st.session_state.connected = False
                st.session_state.clients = None
                st.error(f"Connection failed: {error}")

    if st.sidebar.button("Disconnect"):
        st.session_state.connected = False
        st.session_state.clients = None
        st.session_state.connection = {}
        st.session_state.volume_records = []
        st.session_state.last_backup_results = []
        st.session_state.selected_volume_labels = []

    if not st.session_state.connected or st.session_state.clients is None:
        st.info("Connect to a cluster from the sidebar to start discovery and backup operations.")
        return

    runtime_path_errors = _validate_runtime_paths(
        backup_dir_input=backup_dir_input,
        metadata_db_path_input=metadata_db_path_input,
    )
    if destination_label == _DESTINATION_REMOTE_LABEL:
        runtime_path_errors.extend(
            _validate_remote_destination_inputs(
                host_input=remote_host_input,
                username_input=remote_username_input,
                password_input=remote_password_input,
                directory_input=remote_directory_input,
            )
        )
    if runtime_path_errors:
        for error in runtime_path_errors:
            st.error(error)
        return

    metadata_store = BackupMetadataStore(base_config.metadata_db_path)
    metadata_store.initialize()

    clients = st.session_state.clients
    summary = get_cluster_summary(clients)
    summary_columns = st.columns(3)
    summary_columns[0].metric("Namespaces", summary["namespaces"])
    summary_columns[1].metric("Pods", summary["pods"])
    summary_columns[2].metric("PVCs", summary["persistent_volume_claims"])

    st.subheader("Volume Discovery")
    namespace_filter_input = st.text_input(
        "Namespace filter (comma-separated, optional)",
        value="",
        help="Leave blank to scan all namespaces.",
    )
    namespaces = [value.strip() for value in namespace_filter_input.split(",") if value.strip()]

    if st.button("Refresh volume inventory"):
        with st.spinner("Collecting PVCs and owner mappings..."):
            try:
                last_success_map = metadata_store.get_last_success_map()
                st.session_state.volume_records = list_volume_records(
                    clients,
                    namespaces=namespaces or None,
                    last_success_map=last_success_map,
                    request_timeout_seconds=base_config.discovery_timeout_seconds,
                    max_namespace_scan=base_config.max_namespace_scan,
                )
                st.session_state.selected_volume_labels = []
                if not st.session_state.volume_records:
                    st.warning("Discovery completed, but no PVCs were found for the current filter.")
            except KubernetesDiscoveryError as error:
                st.error(str(error))
            except ValueError as error:
                st.error(f"Invalid discovery configuration: {error}")

    volumes: list[VolumeRecord] = st.session_state.volume_records
    if volumes:
        st.dataframe(_build_volume_rows(volumes), use_container_width=True, hide_index=True)

        st.subheader("Backup Selection")
        labels = [_label_for_volume(volume) for volume in volumes]
        label_to_volume = dict(zip(labels, volumes, strict=False))
        st.session_state.selected_volume_labels = [
            label for label in st.session_state.selected_volume_labels if label in label_to_volume
        ]

        selected_labels = st.multiselect(
            "Choose one or more volumes to backup",
            options=labels,
            key="selected_volume_labels",
        )
        st.caption(f"Selected volumes: {len(selected_labels)}")

        if st.button("Backup selected volumes"):
            if not selected_labels:
                st.warning("Select at least one volume.")
            else:
                selected_volumes = [label_to_volume[label] for label in selected_labels]
                destination_mode = "local"
                configured_backup_dir = Path(backup_dir_input)
                remote_destination: RemoteDestinationConfig | None = None
                if destination_label == _DESTINATION_REMOTE_LABEL:
                    destination_mode = "remote"
                    configured_backup_dir = base_config.backup_dir
                    remote_destination = RemoteDestinationConfig(
                        protocol=_remote_protocol_value(remote_protocol_label),
                        host=remote_host_input.strip(),
                        username=remote_username_input.strip(),
                        password=remote_password_input,
                        directory=remote_directory_input.strip(),
                    )

                manager = BackupManager(
                    core_api=clients.core_api,
                    metadata_store=metadata_store,
                    config=BackupManagerConfig(
                        backup_dir=configured_backup_dir,
                        helper_image=base_config.helper_image,
                        helper_pod_timeout_seconds=base_config.helper_pod_timeout_seconds,
                        kubeconfig_path=st.session_state.connection.get("kubeconfig_path"),
                        context=st.session_state.connection.get("context"),
                        in_cluster_auth=bool(st.session_state.connection.get("in_cluster")),
                        destination_mode=destination_mode,
                        remote_destination=remote_destination,
                    ),
                )
                if batch_settings.mode == "parallel_preview":
                    st.info(
                        "Parallel execution is planned and currently runs sequentially. "
                        f"Requested worker preview={batch_settings.requested_max_workers}."
                    )

                with st.spinner(f"Running backups for {len(selected_volumes)} volume(s)..."):
                    st.session_state.last_backup_results = _run_batch_backup(
                        manager=manager,
                        metadata_store=metadata_store,
                        selected_volumes=selected_volumes,
                        settings=batch_settings,
                    )

                completed_count = len(st.session_state.last_backup_results)
                if batch_settings.stop_on_failure and completed_count < len(selected_volumes):
                    st.warning(
                        f"Stopped early after first failure: completed {completed_count} of "
                        f"{len(selected_volumes)} selected volume(s)."
                    )

                failed_count = sum(1 for result in st.session_state.last_backup_results if result.status != "success")
                if failed_count:
                    st.error(
                        f"Backup run finished with failures: {failed_count} of {completed_count} "
                        "volume(s) failed. Review actionable details below."
                    )
                else:
                    st.success(f"Backup job finished successfully for {completed_count} volume(s).")
    else:
        st.info("Click 'Refresh volume inventory' to load persistent volume claims.")

    if st.session_state.last_backup_results:
        st.subheader("Latest Backup Run")
        latest_rows = _build_result_rows(st.session_state.last_backup_results)
        st.dataframe(
            latest_rows,
            use_container_width=True,
            hide_index=True,
        )
        failed_rows = [row for row in latest_rows if row["status"] != "success"]
        if failed_rows:
            st.markdown("**Actionable Failures**")
            for row in failed_rows:
                st.error(f"{row['namespace']}/{row['pvc']}: {row['actionable_message']}")

    st.subheader("Recent Backup History")
    history_rows = _build_history_rows(metadata_store.get_recent_results(limit=100))
    if history_rows:
        st.dataframe(history_rows, use_container_width=True, hide_index=True)
    else:
        st.info("No backup history yet. Run your first backup to populate this table.")


if __name__ == "__main__":
    main()
