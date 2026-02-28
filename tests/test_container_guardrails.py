from __future__ import annotations

from pathlib import Path

_DOCKERFILE = Path("Dockerfile")
_DOCKERIGNORE = Path(".dockerignore")
_ROOT_README = Path("README.md")
_K8S_README = Path("deploy/k8s/README.md")


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _dockerignore_patterns() -> set[str]:
    patterns: set[str] = set()
    for raw_line in _read_text(_DOCKERIGNORE).splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        patterns.add(line)
    return patterns


def test_dockerfile_with_pinned_build_inputs_declares_python_base_and_kubectl_version() -> None:
    dockerfile = _read_text(_DOCKERFILE)

    assert "ARG PYTHON_IMAGE=python:3.12-slim-bookworm" in dockerfile
    assert "ARG KUBECTL_VERSION=v1.31.0" in dockerfile
    assert "FROM registry.k8s.io/kubectl:${KUBECTL_VERSION} AS kubectl" in dockerfile
    assert "FROM ${PYTHON_IMAGE}" in dockerfile


def test_dockerfile_with_runtime_guardrails_includes_kubectl_non_root_and_deterministic_defaults() -> None:
    dockerfile = _read_text(_DOCKERFILE)

    assert "COPY --from=kubectl /bin/kubectl /usr/local/bin/kubectl" in dockerfile
    assert "kubectl version --client --output=yaml" in dockerfile
    assert "USER ${APP_UID}:${APP_GID}" in dockerfile
    assert "NKVM_BACKUP_DIR=/var/lib/nkvm/backups" in dockerfile
    assert "NKVM_METADATA_DB_PATH=/var/lib/nkvm/data/backups.db" in dockerfile
    assert "NKVM_DEFAULT_AUTH_MODE=in-cluster" in dockerfile


def test_dockerignore_with_sensitive_artifacts_excludes_local_secrets_and_runtime_state() -> None:
    patterns = _dockerignore_patterns()

    expected_patterns = {
        ".git",
        ".venv",
        ".env",
        ".env.*",
        ".kube",
        "**/.kube",
        "*kubeconfig*",
        "id_rsa",
        "*.pem",
        "*.key",
        "backups",
        "data",
    }
    assert expected_patterns.issubset(patterns)


def test_readme_with_guardrail_workflow_documents_build_pin_and_runtime_verification() -> None:
    readme = _read_text(_ROOT_README)

    assert "## Container Runtime Guardrail Verification" in readme
    assert "--build-arg PYTHON_IMAGE=python:3.12-slim-bookworm" in readme
    assert "--build-arg KUBECTL_VERSION=v1.31.0" in readme
    assert "id -u && id -g && whoami" in readme
    assert "which kubectl && kubectl version --client --output=yaml | grep gitVersion" in readme
    assert "$NKVM_BACKUP_DIR" in readme
    assert "/home/nkvm/.kube/config" in readme


def test_k8s_readme_with_predeploy_checks_documents_non_root_and_kubectl_validation() -> None:
    readme = _read_text(_K8S_README)

    assert "## 2) Verify Runtime Guardrails Before Deploy" in readme
    assert "id -u && id -g && whoami" in readme
    assert "which kubectl && kubectl version --client --output=yaml | grep gitVersion" in readme
    assert "$NKVM_DEFAULT_AUTH_MODE" in readme
    assert "default UID/GID `10001`" in readme
