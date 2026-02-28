ARG PYTHON_IMAGE=python:3.12-slim-bookworm
ARG KUBECTL_VERSION=v1.31.0

FROM registry.k8s.io/kubectl:${KUBECTL_VERSION} AS kubectl
FROM ${PYTHON_IMAGE}

ARG APP_USER=nkvm
ARG APP_GROUP=nkvm
ARG APP_UID=10001
ARG APP_GID=10001
ARG APP_HOME=/home/nkvm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    NKVM_BACKUP_DIR=/var/lib/nkvm/backups \
    NKVM_METADATA_DB_PATH=/var/lib/nkvm/data/backups.db \
    NKVM_DEFAULT_AUTH_MODE=in-cluster \
    NKVM_HELPER_IMAGE=alpine:3.20 \
    NKVM_HELPER_POD_TIMEOUT_SECONDS=120 \
    NKVM_DISCOVERY_TIMEOUT_SECONDS=20 \
    NKVM_MAX_NAMESPACE_SCAN=100 \
    HOME=${APP_HOME}

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates openssh-client rsync sshpass \
    && rm -rf /var/lib/apt/lists/*

COPY --from=kubectl /bin/kubectl /usr/local/bin/kubectl
RUN chmod 0755 /usr/local/bin/kubectl \
    && kubectl version --client --output=yaml >/tmp/kubectl-version.yaml \
    && rm -f /tmp/kubectl-version.yaml

WORKDIR /app

COPY pyproject.toml README.md /app/
COPY src /app/src

RUN pip install --no-cache-dir .

RUN groupadd --gid "${APP_GID}" "${APP_GROUP}" \
    && useradd --uid "${APP_UID}" --gid "${APP_GID}" --create-home --home-dir "${APP_HOME}" --shell /usr/sbin/nologin "${APP_USER}" \
    && mkdir -p /var/lib/nkvm/backups /var/lib/nkvm/data \
    && chown -R "${APP_UID}:${APP_GID}" /var/lib/nkvm /app

USER ${APP_UID}:${APP_GID}

EXPOSE 8501

ENTRYPOINT ["streamlit", "run", "src/nerdy_k8s_volume_manager/app.py", "--server.port=8501", "--server.address=0.0.0.0", "--server.headless=true"]
