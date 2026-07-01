"""
Phase 64 — Kubernetes manifest validation tests.

Validates deploy/kubernetes/*.yaml are parseable and meet structural
requirements. Uses PyYAML only — no Kubernetes client needed.
"""
from __future__ import annotations

import os

import pytest
import yaml

K8S_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "deploy", "kubernetes"))

MANIFEST_FILES = {
    "namespace": "namespace.yaml",
    "serviceaccount": "serviceaccount.yaml",
    "configmap": "configmap.yaml",
    "secret": "secret.yaml",
    "pvc": "persistent-volume-claim.yaml",
    "deployment": "deployment.yaml",
    "service": "service.yaml",
    "networkpolicy": "networkpolicy.yaml",
    "pdb": "poddisruptionbudget.yaml",
    "validate-config-job": "job-validate-config.yaml",
}


def _load(name: str) -> dict:
    path = os.path.join(K8S_DIR, name)
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


class TestManifestsParseable:
    """All manifests must be valid YAML with kind field."""

    @pytest.mark.parametrize("name,filename", MANIFEST_FILES.items())
    def test_manifest_parseable(self, name: str, filename: str):
        doc = _load(filename)
        assert isinstance(doc, dict), f"{name}: must parse to dict"
        assert "kind" in doc, f"{name}: missing 'kind' field"


class TestDeployment:
    """Deployment structural requirements."""

    def test_deployment_replicas_is_one(self):
        doc = _load("deployment.yaml")
        spec = doc.get("spec", {})
        assert spec.get("replicas") == 1, "Deployment must have replicas: 1"

    def test_deployment_has_liveness_probe(self):
        doc = _load("deployment.yaml")
        containers = doc.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
        assert containers, "Deployment must have containers"
        probe = containers[0].get("livenessProbe")
        assert probe is not None, "Deployment container must have livenessProbe"

    def test_deployment_has_readiness_probe(self):
        doc = _load("deployment.yaml")
        containers = doc.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
        assert containers, "Deployment must have containers"
        probe = containers[0].get("readinessProbe")
        assert probe is not None, "Deployment container must have readinessProbe"

    def test_deployment_has_startup_probe(self):
        doc = _load("deployment.yaml")
        containers = doc.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
        assert containers, "Deployment must have containers"
        probe = containers[0].get("startupProbe")
        assert probe is not None, "Deployment container must have startupProbe"

    def test_deployment_has_termination_grace_period(self):
        doc = _load("deployment.yaml")
        tgs = doc.get("spec", {}).get("template", {}).get("spec", {}).get("terminationGracePeriodSeconds")
        assert tgs is not None, "Deployment must set terminationGracePeriodSeconds"
        assert isinstance(tgs, int) and tgs >= 30, "terminationGracePeriodSeconds must be >= 30"

    def test_deployment_has_prestop_hook(self):
        doc = _load("deployment.yaml")
        containers = doc.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
        assert containers, "Deployment must have containers"
        lifecycle = containers[0].get("lifecycle", {})
        pre_stop = lifecycle.get("preStop")
        assert pre_stop is not None, "Deployment container must have preStop lifecycle hook"

    def test_deployment_non_root_security_context(self):
        doc = _load("deployment.yaml")
        pod_sc = doc.get("spec", {}).get("template", {}).get("spec", {}).get("securityContext", {})
        assert pod_sc.get("runAsNonRoot") is True, "Pod securityContext must set runAsNonRoot: true"
        containers = doc.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
        assert containers, "Deployment must have containers"
        container_sc = containers[0].get("securityContext", {})
        assert container_sc.get("readOnlyRootFilesystem") is True, (
            "Container securityContext must set readOnlyRootFilesystem: true"
        )

    def test_deployment_mounts_pvc_to_data(self):
        doc = _load("deployment.yaml")
        volumes = doc.get("spec", {}).get("template", {}).get("spec", {}).get("volumes", [])
        pvc_volumes = [v for v in volumes if "persistentVolumeClaim" in v]
        assert pvc_volumes, "Deployment must have a PVC volume"

        containers = doc.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
        assert containers, "Deployment must have containers"
        mounts = containers[0].get("volumeMounts", [])
        data_mounts = [m for m in mounts if m.get("mountPath") == "/data"]
        assert data_mounts, "Deployment container must mount PVC at /data"


class TestConfigMap:
    """ConfigMap content checks."""

    def test_configmap_has_health_http_enabled(self):
        doc = _load("configmap.yaml")
        data = doc.get("data", {})
        config = yaml.safe_load(data.get("daemon.yaml", ""))
        rd = config.get("runtime", {}).get("alerts", {}).get("delivery", {}).get("retry_daemon", {})
        assert "health_http_enabled" in rd, "ConfigMap must contain health_http_enabled"

    def test_configmap_has_control_plane_enabled(self):
        doc = _load("configmap.yaml")
        data = doc.get("data", {})
        config = yaml.safe_load(data.get("daemon.yaml", ""))
        rd = config.get("runtime", {}).get("alerts", {}).get("delivery", {}).get("retry_daemon", {})
        assert "control_plane_enabled" in rd, "ConfigMap must contain control_plane_enabled"

    def test_configmap_has_phase62_fields(self):
        doc = _load("configmap.yaml")
        data = doc.get("data", {})
        config = yaml.safe_load(data.get("daemon.yaml", ""))
        rd = config.get("runtime", {}).get("alerts", {}).get("delivery", {}).get("retry_daemon", {})
        for field in ["graceful_shutdown_enabled", "drain_timeout_seconds", "cancel_inflight_on_timeout",
                       "metrics_buffer_enabled", "flush_metrics_on_stop"]:
            assert field in rd, f"ConfigMap must contain Phase 62 field: {field}"

    def test_configmap_has_phase63_fields(self):
        doc = _load("configmap.yaml")
        data = doc.get("data", {})
        config = yaml.safe_load(data.get("daemon.yaml", ""))
        rd = config.get("runtime", {}).get("alerts", {}).get("delivery", {}).get("retry_daemon", {})
        for field in ["control_plane_enabled", "control_http_enabled", "control_http_port",
                       "control_plane_db_path"]:
            assert field in rd, f"ConfigMap must contain Phase 63 field: {field}"


class TestSecret:
    """Secret content checks."""

    def test_secret_no_real_token(self):
        """Secret must not contain a real-looking token."""
        doc = _load("secret.yaml")
        data = doc.get("data", {})
        token_b64 = data.get("AGENT_APP_CONTROL_TOKEN", "")
        # Decode and check it's the placeholder, not a real token
        import base64
        decoded = base64.b64decode(token_b64).decode("utf-8")
        assert decoded == "dev-token", (
            f"Secret contains non-placeholder token: {decoded!r}. "
            "Replace with real token before deploying."
        )


class TestService:
    """Service structural requirements."""

    def test_service_exposes_health_port(self):
        doc = _load("service.yaml")
        ports = doc.get("spec", {}).get("ports", [])
        port_names = {p.get("name") for p in ports}
        assert "health" in port_names, "Service must expose health port"

    def test_service_exposes_control_port(self):
        doc = _load("service.yaml")
        ports = doc.get("spec", {}).get("ports", [])
        port_names = {p.get("name") for p in ports}
        assert "control" in port_names, "Service must expose control port"

    def test_service_health_port_8080(self):
        doc = _load("service.yaml")
        ports = doc.get("spec", {}).get("ports", [])
        health_ports = [p for p in ports if p.get("name") == "health"]
        assert health_ports, "Service must have health port"
        assert health_ports[0].get("port") == 8080

    def test_service_control_port_8090(self):
        doc = _load("service.yaml")
        ports = doc.get("spec", {}).get("ports", [])
        control_ports = [p for p in ports if p.get("name") == "control"]
        assert control_ports, "Service must have control port"
        assert control_ports[0].get("port") == 8090


class TestPVC:
    """PVC requirements."""

    def test_pvc_exists(self):
        doc = _load("persistent-volume-claim.yaml")
        assert doc.get("kind") == "PersistentVolumeClaim"

    def test_pvc_rw_once(self):
        doc = _load("persistent-volume-claim.yaml")
        access_modes = doc.get("spec", {}).get("accessModes", [])
        assert "ReadWriteOnce" in access_modes, "PVC must use ReadWriteOnce (single-writer SQLite)"

    def test_pvc_mount_data_path(self):
        """Deployment should mount PVC at /data."""
        doc = _load("deployment.yaml")
        containers = doc.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
        assert containers, "Deployment must have containers"
        mounts = containers[0].get("volumeMounts", [])
        data_mounts = [m for m in mounts if m.get("mountPath") == "/data"]
        assert data_mounts, "PVC must be mounted at /data"


class TestNetworkPolicy:
    """NetworkPolicy requirements."""

    def test_networkpolicy_exists(self):
        doc = _load("networkpolicy.yaml")
        assert doc.get("kind") == "NetworkPolicy"
        assert "ingress" in doc.get("spec", {}), "NetworkPolicy must define ingress rules"


class TestPDB:
    """PodDisruptionBudget requirements."""

    def test_pdb_exists(self):
        doc = _load("poddisruptionbudget.yaml")
        assert doc.get("kind") == "PodDisruptionBudget"

    def test_pdb_min_available_zero(self):
        doc = _load("poddisruptionbudget.yaml")
        assert doc.get("spec", {}).get("minAvailable") == 0, (
            "PDB must set minAvailable: 0 for single-replica SQLite deployment"
        )


class TestValidateConfigJob:
    """validate-config Job requirements."""

    def test_job_exists(self):
        doc = _load("job-validate-config.yaml")
        assert doc.get("kind") == "Job"

    def test_job_has_command(self):
        doc = _load("job-validate-config.yaml")
        containers = doc.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
        assert containers, "Job must have containers"
        args = containers[0].get("args", [])
        assert "validate-config" in args, "Job must run validate-config command"
