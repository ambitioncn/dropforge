# Release procedure

Release preparation is local and reviewable. Commit, push, tag, GitHub release,
registry publication, and production deployment are separate human-authorized
actions.

## Prepare

1. Confirm the version in `pyproject.toml` and `src/dropforge/__init__.py`
   matches the intended tag.
2. Review all changes from the last release, including untracked files.
3. Run the readiness checklist and secret scan over the full Git history.
4. Run the test, static, package, and container commands below.
5. Inspect wheel and source-archive contents; they must exclude state,
   credentials, environment files, browser data, and loop artifacts.
6. Record artifact SHA-256 digests and retain the cumulative patch separately.

```bash
python -m unittest discover -s tests -v
python -m compileall -q src tests
ruff check src tests
python -m build
python -m venv /tmp/dropforge-release-check
/tmp/dropforge-release-check/bin/python -m pip install --no-deps dist/*.whl
/tmp/dropforge-release-check/bin/dropforge --help
docker build --tag dropforge:release-candidate .
docker run --rm dropforge:release-candidate --help
```

## Human publication gate

An authenticated maintainer reviews the cumulative diff, CI result, secret-scan
result, package contents, container provenance, and release notes. Only that
maintainer may approve commit/push/tag/publication/deployment. A prepared patch
or passing local test suite does not imply approval.

After publication, verify checksums and installation from the public artifact
without using production credentials. If any artifact differs from the reviewed
candidate, stop and investigate rather than replacing it silently.
