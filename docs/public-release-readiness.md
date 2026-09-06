# Public-release readiness review

Status: **prepared for maintainer review; not approved or published**.

## Ready for review

- [x] Supported Python versions are declared and covered by CI.
- [x] Unit/regression, compile, static, package, container, and secret-scan jobs
  are defined.
- [x] systemd and container examples keep the dashboard on literal loopback.
- [x] Container defaults are unprivileged, read-only, capability-free, and do
  not bundle OpenClaw or private browser material.
- [x] Installation, configuration, operations, security, browser handoff, and
  release procedures are documented.
- [x] License, contributing guide, and private vulnerability-reporting policy
  are present.
- [x] Exact matching, authorization, price ceilings, idempotency, unknown-result
  reconciliation, and human challenge handoff remain explicit guarantees.
- [x] Examples contain placeholders only; runtime secret/state files are
  ignored from build and version-control contexts.

## Maintainer gates

- [ ] Review and apply the cumulative patch to the canonical repository.
- [ ] Choose the public repository owner/name and replace placeholder links.
- [ ] Review and pin GitHub Actions and the Python base image under the
  maintainer's supply-chain policy.
- [ ] Run GitHub-hosted CI and inspect its Gitleaks result.
- [ ] Confirm project metadata, support contact, changelog/release notes, and
  release version.
- [ ] Approve commit, push, tag, package/container publication, and any
  production deployment separately.

Until every applicable gate is complete, the distribution milestone may be
locally accepted but the project is not publicly released.
