from __future__ import annotations

from pathlib import Path
import sqlite3
from typing import Any

from .models import BackupResult


class BackupMetadataStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def initialize(self) -> None:
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS backup_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    pvc_uid TEXT NOT NULL,
                    namespace TEXT NOT NULL,
                    pvc_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    backup_path TEXT,
                    checksum_sha256 TEXT,
                    message TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_backup_history_lookup
                ON backup_history(namespace, pvc_name, status, created_at)
                """
            )
            connection.commit()

    def record_result(self, result: BackupResult) -> None:
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                """
                INSERT INTO backup_history (
                    pvc_uid,
                    namespace,
                    pvc_name,
                    status,
                    backup_path,
                    checksum_sha256,
                    message,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result.pvc_uid,
                    result.namespace,
                    result.pvc_name,
                    result.status,
                    result.backup_path,
                    result.checksum_sha256,
                    result.message,
                    result.finished_at,
                ),
            )
            connection.commit()

    def get_last_success_map(self) -> dict[tuple[str, str], str]:
        with sqlite3.connect(self.db_path) as connection:
            cursor = connection.execute(
                """
                SELECT current.namespace, current.pvc_name, current.created_at
                FROM backup_history AS current
                WHERE current.status = 'success'
                  AND current.id = (
                    SELECT candidate.id
                    FROM backup_history AS candidate
                    WHERE candidate.status = 'success'
                      AND candidate.namespace = current.namespace
                      AND candidate.pvc_name = current.pvc_name
                    ORDER BY candidate.created_at DESC, candidate.id DESC
                    LIMIT 1
                  )
                """
            )
            rows = cursor.fetchall()

        return {(namespace, pvc_name): last_success for namespace, pvc_name, last_success in rows}

    def get_recent_results(self, limit: int = 50) -> list[dict[str, Any]]:
        if limit <= 0:
            return []

        with sqlite3.connect(self.db_path) as connection:
            cursor = connection.execute(
                """
                SELECT namespace, pvc_name, status, backup_path, checksum_sha256, message, created_at
                FROM backup_history
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (limit,),
            )
            rows = cursor.fetchall()

        return [
            {
                "namespace": row[0],
                "pvc_name": row[1],
                "status": row[2],
                "backup_path": row[3],
                "checksum_sha256": row[4],
                "message": row[5],
                "created_at": row[6],
            }
            for row in rows
        ]

    def count_results(self) -> int:
        with sqlite3.connect(self.db_path) as connection:
            cursor = connection.execute("SELECT COUNT(*) FROM backup_history")
            row = cursor.fetchone()

        return int(row[0]) if row else 0

    def get_retention_candidate_ids(self, keep_latest: int) -> list[int]:
        if keep_latest < 0:
            raise ValueError("keep_latest must be >= 0")

        with sqlite3.connect(self.db_path) as connection:
            cursor = connection.execute(
                """
                SELECT id
                FROM backup_history
                ORDER BY created_at DESC, id DESC
                LIMIT -1 OFFSET ?
                """,
                (keep_latest,),
            )
            rows = cursor.fetchall()

        return [int(row[0]) for row in rows]
