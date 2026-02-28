from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterator
from urllib.parse import urlparse
import base64
import os
import shlex
import shutil
import subprocess
import time
import uuid

import pytest
import yaml

_ENV_RUN_FLAG = "NKVM_RUN_KIND_INTEGRATION"
_MANIFEST_PATH = Path(__file__).parent / "manifests" / "smoke-pvc-pod.yaml"
_AUTH_RBAC_MANIFEST_PATH = Path(__file__).parent / "manifests" / "smoke-auth-rbac.yaml"
_REQUIRED_BINARIES = ("docker", "kind", "kubectl")


@dataclass(frozen=True)
class InClusterAuthMaterial:
    service_account_name: str
    token_path: Path
    ca_path: Path
    service_host: str
    service_port: str


def _flag_enabled(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _render_command(command: list[str]) -> str:
    return " ".join(shlex.quote(token) for token in command)


def _run_command(
    command: list[str],
    *,
    timeout_seconds: int,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
    )

    if check and completed.returncode != 0:
        stdout = completed.stdout.strip() or "<empty>"
        stderr = completed.stderr.strip() or "<empty>"
        raise RuntimeError(
            f"Command failed with exit code {completed.returncode}: {_render_command(command)}\n"
            f"stdout:\n{stdout}\n"
            f"stderr:\n{stderr}"
        )

    return completed


def _verify_prerequisites() -> None:
    if not _flag_enabled(os.getenv(_ENV_RUN_FLAG)):
        pytest.skip(
            "KinD integration tests are disabled by default. "
            f"Set {_ENV_RUN_FLAG}=1 to run them.",
            allow_module_level=True,
        )

    missing = [binary for binary in _REQUIRED_BINARIES if shutil.which(binary) is None]
    if missing:
        missing_rendered = ", ".join(sorted(missing))
        pytest.skip(
            f"KinD integration prerequisites are missing: {missing_rendered}.",
            allow_module_level=True,
        )

    docker_info = _run_command(
        ["docker", "info", "--format", "{{.ServerVersion}}"],
        timeout_seconds=30,
        check=False,
    )
    if docker_info.returncode != 0:
        stderr = docker_info.stderr.strip() or docker_info.stdout.strip() or "unknown docker error"
        pytest.skip(
            f"Docker daemon is not reachable for KinD integration tests: {stderr}.",
            allow_module_level=True,
        )


@dataclass(frozen=True)
class KindClusterContext:
    cluster_name: str
    kubeconfig_path: Path
    remote_kubeconfig_secret_path: Path
    harness_dir: Path
    namespace: str
    pvc_name: str
    source_pod_name: str
    runtime_service_account: str
    unbound_service_account: str

    def run_kubectl(
        self,
        *args: str,
        timeout_seconds: int = 120,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        return _run_command(
            ["kubectl", "--kubeconfig", str(self.kubeconfig_path), *args],
            timeout_seconds=timeout_seconds,
            check=check,
        )

    def service_account_identity(self, service_account_name: str) -> str:
        return f"system:serviceaccount:{self.namespace}:{service_account_name}"

    def mint_service_account_token(self, *, service_account_name: str) -> str:
        completed = self.run_kubectl(
            "-n",
            self.namespace,
            "create",
            "token",
            service_account_name,
            "--duration=10m",
            timeout_seconds=60,
            check=False,
        )
        if completed.returncode != 0 and "unknown flag: --duration" in (
            f"{completed.stderr}\n{completed.stdout}".lower()
        ):
            completed = self.run_kubectl(
                "-n",
                self.namespace,
                "create",
                "token",
                service_account_name,
                timeout_seconds=60,
                check=False,
            )

        if completed.returncode != 0:
            stdout = completed.stdout.strip() or "<empty>"
            stderr = completed.stderr.strip() or "<empty>"
            raise RuntimeError(
                "Unable to mint ServiceAccount token for KinD integration tests. "
                f"service_account={service_account_name}\n"
                f"stdout:\n{stdout}\n"
                f"stderr:\n{stderr}"
            )

        token = completed.stdout.strip()
        if not token:
            raise RuntimeError(
                "ServiceAccount token generation returned empty output. "
                f"service_account={service_account_name}"
            )
        return token

    def build_incluster_auth_material(self, *, service_account_name: str) -> InClusterAuthMaterial:
        host, port, ca_pem = self._read_cluster_server_and_ca()
        token = self.mint_service_account_token(service_account_name=service_account_name)

        token_path = self.harness_dir / f"{service_account_name}.token"
        ca_path = self.harness_dir / f"{service_account_name}.ca.crt"
        token_path.write_text(token, encoding="utf-8")
        ca_path.write_text(ca_pem, encoding="utf-8")
        os.chmod(token_path, 0o600)
        os.chmod(ca_path, 0o600)

        return InClusterAuthMaterial(
            service_account_name=service_account_name,
            token_path=token_path,
            ca_path=ca_path,
            service_host=host,
            service_port=port,
        )

    def collect_diagnostics(self) -> str:
        runner_identity = self.service_account_identity(self.runtime_service_account)
        unbound_identity = self.service_account_identity(self.unbound_service_account)

        diagnostic_commands: tuple[tuple[str, list[str]], ...] = (
            ("kubectl version", ["version", "--client"]),
            ("nodes", ["get", "nodes", "-o", "wide"]),
            ("pods", ["-n", self.namespace, "get", "pods", "-o", "wide"]),
            ("pvc/pv", ["-n", self.namespace, "get", "pvc,pv"]),
            (
                "events",
                ["-n", self.namespace, "get", "events", "--sort-by=.lastTimestamp"],
            ),
            (
                "source-pod-describe",
                ["-n", self.namespace, "describe", "pod", self.source_pod_name],
            ),
            (
                "source-pod-logs",
                ["-n", self.namespace, "logs", self.source_pod_name],
            ),
            (
                "helper-pods",
                [
                    "-n",
                    self.namespace,
                    "get",
                    "pods",
                    "-l",
                    "app.kubernetes.io/component=backup-helper",
                    "-o",
                    "wide",
                ],
            ),
            (
                "rbac-runner-can-list-pvc",
                [
                    "auth",
                    "can-i",
                    f"--as={runner_identity}",
                    "list",
                    "persistentvolumeclaims",
                    "-n",
                    self.namespace,
                ],
            ),
            (
                "rbac-runner-can-create-pods",
                [
                    "auth",
                    "can-i",
                    f"--as={runner_identity}",
                    "create",
                    "pods",
                    "-n",
                    self.namespace,
                ],
            ),
            (
                "rbac-unbound-can-create-pods",
                [
                    "auth",
                    "can-i",
                    f"--as={unbound_identity}",
                    "create",
                    "pods",
                    "-n",
                    self.namespace,
                ],
            ),
        )

        sections: list[str] = []
        for title, args in diagnostic_commands:
            completed = self.run_kubectl(*args, timeout_seconds=60, check=False)
            output = completed.stdout.strip() or completed.stderr.strip() or "<no output>"
            sections.append(f"[{title}]\n{output}")

        return "\n\n".join(sections)

    def _read_cluster_server_and_ca(self) -> tuple[str, str, str]:
        raw_kubeconfig = self.kubeconfig_path.read_text(encoding="utf-8")
        parsed = yaml.safe_load(raw_kubeconfig)

        if not isinstance(parsed, dict):
            raise RuntimeError("KinD kubeconfig must be a YAML mapping.")

        clusters = parsed.get("clusters")
        if not isinstance(clusters, list) or not clusters:
            raise RuntimeError("KinD kubeconfig did not contain any clusters.")

        cluster_entry = clusters[0]
        if not isinstance(cluster_entry, dict):
            raise RuntimeError("KinD kubeconfig has invalid cluster entry shape.")

        cluster_block = cluster_entry.get("cluster")
        if not isinstance(cluster_block, dict):
            raise RuntimeError("KinD kubeconfig cluster entry is missing the 'cluster' mapping.")

        server = str(cluster_block.get("server", "")).strip()
        parsed_server = urlparse(server)
        if parsed_server.scheme != "https" or not parsed_server.hostname:
            raise RuntimeError(f"Unable to parse Kubernetes API server endpoint from kubeconfig: {server!r}")

        service_host = parsed_server.hostname
        service_port = str(parsed_server.port or 443)

        ca_data = cluster_block.get("certificate-authority-data")
        if isinstance(ca_data, str) and ca_data.strip():
            try:
                decoded = base64.b64decode(ca_data)
                return service_host, service_port, decoded.decode("utf-8")
            except Exception as error:  # pylint: disable=broad-except
                raise RuntimeError("Failed to decode certificate-authority-data from kubeconfig.") from error

        ca_file_raw = str(cluster_block.get("certificate-authority", "")).strip()
        if ca_file_raw:
            ca_file = Path(ca_file_raw)
            if not ca_file.is_absolute():
                ca_file = (self.kubeconfig_path.parent / ca_file).resolve()
            if not ca_file.exists():
                raise RuntimeError(f"certificate-authority file from kubeconfig does not exist: {ca_file}")
            return service_host, service_port, ca_file.read_text(encoding="utf-8")

        raise RuntimeError("Kubeconfig did not include certificate-authority-data or certificate-authority.")


def _materialize_remote_kubeconfig_secret_mount(*, source_kubeconfig_path: Path, harness_dir: Path) -> Path:
    remote_secret_path = harness_dir / "etc" / "nkvm" / "remote" / "config"
    remote_secret_path.parent.mkdir(parents=True, exist_ok=True)
    remote_secret_path.write_text(source_kubeconfig_path.read_text(encoding="utf-8"), encoding="utf-8")
    os.chmod(remote_secret_path, 0o600)
    return remote_secret_path


def _wait_for_pvc_bound(cluster: KindClusterContext, *, timeout_seconds: int) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        completed = cluster.run_kubectl(
            "-n",
            cluster.namespace,
            "get",
            "pvc",
            cluster.pvc_name,
            "-o",
            "jsonpath={.status.phase}",
            timeout_seconds=30,
            check=False,
        )
        if completed.returncode == 0 and completed.stdout.strip() == "Bound":
            return
        time.sleep(2)

    raise RuntimeError(f"PVC {cluster.namespace}/{cluster.pvc_name} did not become Bound in time.")


@pytest.fixture(scope="session")
def kind_cluster(tmp_path_factory: pytest.TempPathFactory) -> Iterator[KindClusterContext]:
    _verify_prerequisites()
    for manifest_path in (_MANIFEST_PATH, _AUTH_RBAC_MANIFEST_PATH):
        if not manifest_path.exists():
            raise RuntimeError(f"Expected KinD integration manifest at {manifest_path}.")

    harness_dir = tmp_path_factory.mktemp("kind-harness")
    kubeconfig_path = harness_dir / "kubeconfig"
    cluster_name = f"nkvm-it-{uuid.uuid4().hex[:8]}"

    _run_command(
        [
            "kind",
            "create",
            "cluster",
            "--name",
            cluster_name,
            "--wait",
            "180s",
            "--kubeconfig",
            str(kubeconfig_path),
        ],
        timeout_seconds=420,
    )

    remote_kubeconfig_secret_path = _materialize_remote_kubeconfig_secret_mount(
        source_kubeconfig_path=kubeconfig_path,
        harness_dir=harness_dir,
    )

    cluster = KindClusterContext(
        cluster_name=cluster_name,
        kubeconfig_path=kubeconfig_path,
        remote_kubeconfig_secret_path=remote_kubeconfig_secret_path,
        harness_dir=harness_dir,
        namespace="nkvm-integration",
        pvc_name="nkvm-smoke-data",
        source_pod_name="nkvm-source-pod",
        runtime_service_account="nkvm-runner",
        unbound_service_account="nkvm-unbound",
    )

    try:
        cluster.run_kubectl("apply", "-f", str(_MANIFEST_PATH), timeout_seconds=180)
        cluster.run_kubectl("apply", "-f", str(_AUTH_RBAC_MANIFEST_PATH), timeout_seconds=180)

        cluster.run_kubectl(
            "-n",
            cluster.namespace,
            "get",
            "serviceaccount",
            cluster.runtime_service_account,
            timeout_seconds=60,
        )
        cluster.run_kubectl(
            "-n",
            cluster.namespace,
            "get",
            "serviceaccount",
            cluster.unbound_service_account,
            timeout_seconds=60,
        )

        _wait_for_pvc_bound(cluster, timeout_seconds=180)
        cluster.run_kubectl(
            "-n",
            cluster.namespace,
            "wait",
            "--for=condition=Ready",
            f"pod/{cluster.source_pod_name}",
            "--timeout=180s",
            timeout_seconds=240,
        )
        yield cluster
    finally:
        _run_command(
            ["kind", "delete", "cluster", "--name", cluster_name],
            timeout_seconds=240,
            check=False,
        )
