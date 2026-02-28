from pathlib import Path

import pytest

from nerdy_k8s_volume_manager.metadata import BackupMetadataStore
from nerdy_k8s_volume_manager.models import BackupResult


def _result(
    *,
    namespace: str = "apps",
    pvc_name: str,
    pvc_uid: str,
    status: str,
    finished_at: str,
    started_at: str = "2026-02-23T10:00:00+00:00",
    backup_path: str | None = None,
    checksum_sha256: str | None = None,
    message: str = "",
) -> BackupResult:
    return BackupResult(
        namespace=namespace,
        pvc_name=pvc_name,
        pvc_uid=pvc_uid,
        status=status,
        started_at=started_at,
        finished_at=finished_at,
        backup_path=backup_path,
        checksum_sha256=checksum_sha256,
        message=message,
    )


def test_get_last_success_map_tracks_latest_success_timestamp(tmp_path: Path) -> None:
    store = BackupMetadataStore(tmp_path / "backups.db")
    store.initialize()

    store.record_result(
        _result(
            pvc_name="data-a",
            pvc_uid="uid-1",
            status="failed",
            finished_at="2026-02-23T10:01:00+00:00",
            message="simulated failure",
        )
    )
    store.record_result(
        _result(
            pvc_name="data-a",
            pvc_uid="uid-1",
            status="success",
            finished_at="2026-02-23T11:01:00+00:00",
            backup_path="/tmp/data-a.tar.gz",
            checksum_sha256="abc",
        )
    )

    last_success = store.get_last_success_map()

    assert last_success[("apps", "data-a")] == "2026-02-23T11:01:00+00:00"


def test_get_last_success_map_with_mixed_statuses_returns_successful_entries_only(tmp_path: Path) -> None:
    store = BackupMetadataStore(tmp_path / "backups.db")
    store.initialize()

    store.record_result(
        _result(
            pvc_name="data-a",
            pvc_uid="uid-a",
            status="success",
            finished_at="2026-02-23T09:01:00+00:00",
        )
    )
    store.record_result(
        _result(
            pvc_name="data-a",
            pvc_uid="uid-a",
            status="failed",
            finished_at="2026-02-23T12:01:00+00:00",
            message="post-success failure",
        )
    )
    store.record_result(
        _result(
            pvc_name="data-b",
            pvc_uid="uid-b",
            status="failed",
            finished_at="2026-02-23T11:01:00+00:00",
        )
    )
    store.record_result(
        _result(
            pvc_name="data-c",
            pvc_uid="uid-c",
            status="success",
            finished_at="2026-02-23T10:01:00+00:00",
        )
    )
    store.record_result(
        _result(
            pvc_name="data-c",
            pvc_uid="uid-c",
            status="success",
            finished_at="2026-02-23T13:01:00+00:00",
        )
    )

    last_success = store.get_last_success_map()

    assert last_success[("apps", "data-a")] == "2026-02-23T09:01:00+00:00"
    assert last_success[("apps", "data-c")] == "2026-02-23T13:01:00+00:00"
    assert ("apps", "data-b") not in last_success


def test_get_last_success_map_with_empty_history_returns_empty_map(tmp_path: Path) -> None:
    store = BackupMetadataStore(tmp_path / "backups.db")
    store.initialize()

    assert store.get_last_success_map() == {}


def test_get_recent_results_returns_most_recent_first(tmp_path: Path) -> None:
    store = BackupMetadataStore(tmp_path / "backups.db")
    store.initialize()

    store.record_result(
        _result(
            pvc_name="data-a",
            pvc_uid="uid-1",
            status="success",
            finished_at="2026-02-23T11:01:00+00:00",
            backup_path="/tmp/a.tar.gz",
            checksum_sha256="abc",
        )
    )
    store.record_result(
        _result(
            pvc_name="data-b",
            pvc_uid="uid-2",
            status="success",
            finished_at="2026-02-23T12:01:00+00:00",
            backup_path="/tmp/b.tar.gz",
            checksum_sha256="def",
        )
    )

    rows = store.get_recent_results(limit=10)

    assert len(rows) == 2
    assert rows[0]["pvc_name"] == "data-b"
    assert rows[1]["pvc_name"] == "data-a"


def test_get_recent_results_with_same_timestamp_orders_by_latest_insert(tmp_path: Path) -> None:
    store = BackupMetadataStore(tmp_path / "backups.db")
    store.initialize()

    timestamp = "2026-02-23T12:01:00+00:00"
    store.record_result(_result(pvc_name="data-a", pvc_uid="uid-a", status="success", finished_at=timestamp))
    store.record_result(_result(pvc_name="data-b", pvc_uid="uid-b", status="failed", finished_at=timestamp))

    rows = store.get_recent_results(limit=2)

    assert [rows[0]["pvc_name"], rows[1]["pvc_name"]] == ["data-b", "data-a"]


def test_get_recent_results_with_empty_history_returns_empty_list(tmp_path: Path) -> None:
    store = BackupMetadataStore(tmp_path / "backups.db")
    store.initialize()

    assert store.get_recent_results(limit=10) == []


def test_get_recent_results_with_non_positive_limit_returns_empty_list(tmp_path: Path) -> None:
    store = BackupMetadataStore(tmp_path / "backups.db")
    store.initialize()
    store.record_result(_result(pvc_name="data-a", pvc_uid="uid-a", status="success", finished_at="2026-02-23T11:01:00+00:00"))

    assert store.get_recent_results(limit=0) == []
    assert store.get_recent_results(limit=-1) == []


def test_count_results_returns_total_history_rows(tmp_path: Path) -> None:
    store = BackupMetadataStore(tmp_path / "backups.db")
    store.initialize()
    store.record_result(_result(pvc_name="data-a", pvc_uid="uid-a", status="success", finished_at="2026-02-23T11:01:00+00:00"))
    store.record_result(_result(pvc_name="data-b", pvc_uid="uid-b", status="failed", finished_at="2026-02-23T11:02:00+00:00"))

    assert store.count_results() == 2


def test_get_retention_candidate_ids_returns_rows_past_keep_latest(tmp_path: Path) -> None:
    store = BackupMetadataStore(tmp_path / "backups.db")
    store.initialize()

    store.record_result(_result(pvc_name="data-a", pvc_uid="uid-a", status="success", finished_at="2026-02-23T11:01:00+00:00"))
    store.record_result(_result(pvc_name="data-b", pvc_uid="uid-b", status="success", finished_at="2026-02-23T12:01:00+00:00"))
    store.record_result(_result(pvc_name="data-c", pvc_uid="uid-c", status="failed", finished_at="2026-02-23T13:01:00+00:00"))

    all_ids = store.get_retention_candidate_ids(keep_latest=0)
    retention_candidates = store.get_retention_candidate_ids(keep_latest=2)

    assert len(retention_candidates) == 1
    assert retention_candidates[0] == all_ids[-1]


def test_get_retention_candidate_ids_with_same_timestamp_uses_insert_order(tmp_path: Path) -> None:
    store = BackupMetadataStore(tmp_path / "backups.db")
    store.initialize()

    timestamp = "2026-02-23T14:01:00+00:00"
    store.record_result(_result(pvc_name="data-a", pvc_uid="uid-a", status="success", finished_at=timestamp))
    store.record_result(_result(pvc_name="data-b", pvc_uid="uid-b", status="success", finished_at=timestamp))
    store.record_result(_result(pvc_name="data-c", pvc_uid="uid-c", status="success", finished_at=timestamp))

    all_ids = store.get_retention_candidate_ids(keep_latest=0)
    keep_two_candidates = store.get_retention_candidate_ids(keep_latest=2)

    assert len(all_ids) == 3
    assert len(keep_two_candidates) == 1
    assert keep_two_candidates[0] == all_ids[-1]


def test_get_retention_candidate_ids_with_negative_keep_latest_raises_value_error(tmp_path: Path) -> None:
    store = BackupMetadataStore(tmp_path / "backups.db")
    store.initialize()

    with pytest.raises(ValueError):
        store.get_retention_candidate_ids(keep_latest=-1)
