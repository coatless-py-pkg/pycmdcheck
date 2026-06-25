# Changelog

All notable changes to pycmdcheck will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.1] - 2026-06-25

### Fixed

- Project URLs now point to the actual repository
  (`github.com/coatless-py-pkg/pycmdcheck`); the previously declared
  `pycmdcheck/pycmdcheck` URLs referenced a repository that does not exist (404).
- Installation instructions use `pip install pycmdcheck` now that the package is
  published on PyPI.

### Changed

- Updated GitHub Actions to current versions (`checkout` v7, `setup-uv` v8.2.0,
  `upload-artifact` v7, `download-artifact` v8, `gh-action-pypi-publish` v1.14.0).

## [0.1.0] - 2026-06-25

First release of pycmdcheck on PyPI.

### Added

- Core `check()` function and `pycmdcheck` CLI, with Rich terminal output and
  JSON output for CI integration.
- 20 built-in checks: `metadata`, `structure`, `tests`, `linting`, `typing`,
  `imports`, `license`, `docs`, `dependencies`, `build`, `formatting`,
  `version`, `py_typed`, `community`, `ci`, `changelog`, `citation`,
  `python_versions`, `urls`, `doctests`.
- Profile system — `minimal`, `triage`, `default`, `pyopensci`, `strict` — with
  `--profile` and `--list-profiles`. The `triage` profile runs only static,
  no-install/no-network checks, intended for CI triage of arbitrary
  (un-installed) repositories.
- Configuration via `[tool.pycmdcheck]` in pyproject.toml, a plugin system via
  entry points for custom checks, and parallel check execution.
- Support for legacy Poetry projects (`[tool.poetry]` with no PEP 621
  `[project]` table) across the metadata, version, python_versions, and
  dependencies checks.
- Recognition of PEP 420 namespace packages, single-file modules, and
  native-extension build backends (maturin/scikit-build/setuptools-rust) in the
  `structure` check.
- `formatting` check auto-detects the configured formatter (ruff vs black) and
  is skipped when none is configured.
- GitHub Actions: CI (lint + type-check + pytest on 3.10–3.13) and a
  tag-triggered PyPI release via Trusted Publishing (OIDC).
- Public API: `CheckResult`, `CheckStatus`, `Report`, and the `BaseCheck`
  abstract class for custom checks.

The checks are intentionally lenient to avoid false positives on legitimate
real-world packages (PEP 621 `dynamic` fields, dual-license filenames, RST
READMEs, community files under `.github/`/`docs/`, dependency extras, and more).
