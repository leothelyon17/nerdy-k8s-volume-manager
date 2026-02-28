from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os


@dataclass(frozen=True)
class AppConfig:
    backup_dir: Path = Path(os.getenv("NKVM_BACKUP_DIR", "./backups"))
    metadata_db_path: Path = Path(os.getenv("NKVM_METADATA_DB_PATH", "./data/backups.db"))
    helper_image: str = os.getenv("NKVM_HELPER_IMAGE", "alpine:3.20")
    helper_pod_timeout_seconds: int = int(os.getenv("NKVM_HELPER_POD_TIMEOUT_SECONDS", "120"))
    discovery_timeout_seconds: int = int(os.getenv("NKVM_DISCOVERY_TIMEOUT_SECONDS", "20"))
    max_namespace_scan: int = int(os.getenv("NKVM_MAX_NAMESPACE_SCAN", "100"))


def ensure_directories(config: AppConfig) -> None:
    config.backup_dir.mkdir(parents=True, exist_ok=True)
    config.metadata_db_path.parent.mkdir(parents=True, exist_ok=True)
