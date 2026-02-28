from __future__ import annotations

from pathlib import Path

import yaml

_RBAC_MANIFEST_DIR = Path("deploy/k8s/rbac")


def _read_yaml_document(file_name: str) -> dict:
    return yaml.safe_load((_RBAC_MANIFEST_DIR / file_name).read_text(encoding="utf-8"))


def _cluster_role_rule_map(file_name: str) -> dict[tuple[str, str], set[str]]:
    cluster_role = _read_yaml_document(file_name)
    rule_map: dict[tuple[str, str], set[str]] = {}
    for rule in cluster_role["rules"]:
        api_groups = rule.get("apiGroups", [""])
        resources = rule.get("resources", [])
        verbs = set(rule.get("verbs", []))
        for api_group in api_groups:
            for resource in resources:
                key = (api_group, resource)
                rule_map.setdefault(key, set()).update(verbs)
    return rule_map


def test_runtime_clusterrole_with_namespace_bound_permissions_has_expected_rule_set() -> None:
    rule_map = _cluster_role_rule_map("clusterrole-runtime.yaml")

    assert rule_map == {
        ("", "persistentvolumeclaims"): {"list"},
        ("", "pods"): {"create", "delete", "get", "list"},
        ("", "pods/exec"): {"create", "get"},
        ("apps", "replicasets"): {"get"},
        ("batch", "jobs"): {"get"},
    }
    assert "*" not in rule_map
    assert all("*" not in resource for _, resource in rule_map)
    assert all("*" not in verbs for verbs in rule_map.values())


def test_cluster_discovery_role_with_read_only_permissions_has_expected_rule_set() -> None:
    rule_map = _cluster_role_rule_map("clusterrole-cluster-discovery.yaml")

    assert rule_map == {
        ("", "namespaces"): {"list"},
        ("", "persistentvolumeclaims"): {"list"},
        ("", "pods"): {"list"},
        ("apps", "replicasets"): {"get"},
        ("batch", "jobs"): {"get"},
    }
    assert all(verbs.issubset({"get", "list"}) for verbs in rule_map.values())


def test_cluster_discovery_binding_with_runner_service_account_references_expected_role() -> None:
    binding = _read_yaml_document("clusterrolebinding-cluster-discovery.yaml")

    assert binding["roleRef"] == {
        "apiGroup": "rbac.authorization.k8s.io",
        "kind": "ClusterRole",
        "name": "nkvm-cluster-discovery",
    }
    assert binding["subjects"] == [
        {
            "kind": "ServiceAccount",
            "name": "nkvm-runner",
            "namespace": "nerdy-k8s-volume-manager",
        }
    ]


def test_runtime_rolebinding_template_with_namespace_placeholder_targets_runtime_clusterrole() -> None:
    rolebinding = _read_yaml_document("rolebinding-runtime-template.yaml")

    assert rolebinding["metadata"]["namespace"] == "REPLACE_WITH_ALLOWED_NAMESPACE"
    assert rolebinding["roleRef"] == {
        "apiGroup": "rbac.authorization.k8s.io",
        "kind": "ClusterRole",
        "name": "nkvm-runtime",
    }
    assert rolebinding["subjects"] == [
        {
            "kind": "ServiceAccount",
            "name": "nkvm-runner",
            "namespace": "nerdy-k8s-volume-manager",
        }
    ]


def test_rbac_readme_with_namespace_onboarding_workflow_contains_preflight_guidance() -> None:
    readme = Path("deploy/k8s/rbac/README.md").read_text(encoding="utf-8")

    assert "## Namespace Onboarding Workflow" in readme
    assert "kubectl auth can-i" in readme
    assert "REPLACE_WITH_ALLOWED_NAMESPACE" in readme
    assert "Namespace Offboarding" in readme


def test_security_baseline_with_rbac_validation_checklist_contains_pass_fail_controls() -> None:
    baseline = Path("docs/operations/security-baseline.md").read_text(encoding="utf-8")

    assert "## 3) RBAC Validation Checklist (Pass/Fail)" in baseline
    assert "Runtime Access in Onboarded Namespace (Must Pass)" in baseline
    assert "Runtime Access Outside Allowlist (Must Fail)" in baseline
    assert 'create pods -n "${UNBOUND_NS}"' in baseline
    assert "Fail the release preflight if any check does not match expected output." in baseline
