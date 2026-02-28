from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class VolumeRecord:
    namespace: str
    pvc_name: str
    pvc_uid: str
    phase: str
    capacity: str | None
    storage_class: str | None
    access_modes: tuple[str, ...]
    bound_pv: str | None
    app_kind: str | None
    app_name: str | None
    last_successful_backup_at: str | None


@dataclass(frozen=True)
class BackupResult:
    namespace: str
    pvc_name: str
    pvc_uid: str
    status: str
    started_at: str
    finished_at: str
    backup_path: str | None = None
    checksum_sha256: str | None = None
    message: str = ""
