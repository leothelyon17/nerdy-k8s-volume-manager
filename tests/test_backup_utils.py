from nerdy_k8s_volume_manager.backup import _sanitize_dns_label, _sanitize_filesystem_component


def test_sanitize_dns_label_respects_length_and_charset() -> None:
    raw = "Team_A/Analytics.PVC.With.Really.Long-Name"
    sanitized = _sanitize_dns_label(raw, max_length=20)

    assert len(sanitized) <= 20
    assert sanitized.replace("-", "").isalnum()


def test_sanitize_filesystem_component_replaces_unsupported_chars() -> None:
    assert _sanitize_filesystem_component("apps/db:data") == "apps_db_data"
