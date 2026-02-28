# WP-005 Completion Summary

## Metadata
- Work Package: `WP-005`
- Title: Docker Runtime and Supply-Chain Guardrails
- Date: `2026-02-23`
- Status: `COMPLETE`

## Deliverables
- [x] Hardened container build/runtime guardrails in:
  - `Dockerfile`
  - `.dockerignore`
- [x] Verified non-root runtime identity, required `kubectl` binary, and deterministic runtime env defaults.
- [x] Added container run/verification workflow to:
  - `README.md`
  - `deploy/k8s/README.md`
- [x] Added regression coverage in `tests/test_container_guardrails.py`.

## Acceptance Criteria
- [x] Image includes `kubectl` and Streamlit runtime with non-root user.
- [x] Build reproducibility inputs are documented (base image + kubectl version pin).
- [x] Standalone run flow remains functional with mounted kubeconfig.
- [x] No sensitive local artifacts are included in build context.

## Validation Run
- `./.venv/bin/pytest -q tests/test_container_guardrails.py` -> `5 passed`
- `./.venv/bin/ruff check tests/test_container_guardrails.py` -> `All checks passed`
- `./.venv/bin/pytest -q` -> `97 passed, 2 skipped`
- `docker build --build-arg PYTHON_IMAGE=python:3.12-slim-bookworm --build-arg KUBECTL_VERSION=v1.31.0 -t nkvm:wp005 .` -> `Success`
- `docker run --rm --entrypoint /bin/sh nkvm:wp005 -c 'id -u && id -g && whoami'` -> `10001`, `10001`, `nkvm`
- `docker run --rm --entrypoint /bin/sh nkvm:wp005 -c 'which kubectl && kubectl version --client --output=yaml | grep gitVersion'` -> `/usr/local/bin/kubectl`, `gitVersion: v1.31.0`
- `docker run --rm --entrypoint /bin/sh nkvm:wp005 -c 'printf "%s\n%s\n%s\n" "$NKVM_BACKUP_DIR" "$NKVM_METADATA_DB_PATH" "$NKVM_DEFAULT_AUTH_MODE"'` -> expected defaults
- Standalone smoke test with mounted kubeconfig (`timeout 20s docker run ... -e NKVM_DEFAULT_AUTH_MODE=kubeconfig nkvm:wp005`) -> `streamlit-started` (timeout expected after startup check).

## Notes
- Docker build emitted a non-blocking lint warning (`SecretsUsedInArgOrEnv`) for `NKVM_DEFAULT_AUTH_MODE`; this env key is a non-secret runtime mode selector and remains intentional.
