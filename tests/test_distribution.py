from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DistributionArtifactTests(unittest.TestCase):
    def test_dashboard_deployments_are_literal_loopback(self):
        service = (ROOT / "deploy/systemd/dropforge.service").read_text()
        compose = (ROOT / "compose.yaml").read_text()
        dockerfile = (ROOT / "Dockerfile").read_text()
        for artifact in (service, compose, dockerfile):
            self.assertIn("127.0.0.1", artifact)
            self.assertNotIn("0.0.0.0", artifact)
        self.assertIn("network_mode: host", compose)
        self.assertNotIn("ports:", compose)

    def test_container_and_service_have_hardening_controls(self):
        service = (ROOT / "deploy/systemd/dropforge.service").read_text()
        compose = (ROOT / "compose.yaml").read_text()
        self.assertIn("NoNewPrivileges=true", service)
        self.assertIn("ProtectSystem=strict", service)
        self.assertIn('cap_drop: ["ALL"]', compose)
        self.assertIn("read_only: true", compose)

    def test_ci_covers_supported_versions_and_security(self):
        workflow = (ROOT / ".github/workflows/ci.yml").read_text()
        for version in ("3.11", "3.12", "3.13", "3.14"):
            self.assertIn(version, workflow)
        for check in ("unittest", "compileall", "ruff check", "gitleaks", "docker build"):
            self.assertIn(check, workflow.lower())

    def test_required_operator_guides_exist(self):
        for name in (
            "installation.md",
            "configuration.md",
            "operations.md",
            "security.md",
            "browser-handoff.md",
            "release.md",
            "public-release-readiness.md",
        ):
            path = ROOT / "docs" / name
            self.assertTrue(path.is_file(), name)
            self.assertGreater(len(path.read_text()), 400, name)


if __name__ == "__main__":
    unittest.main()
