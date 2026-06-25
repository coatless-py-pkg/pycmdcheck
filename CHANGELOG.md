# Changelog

All notable changes to pycmdcheck will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- 7 additional built-in checks (bringing the total to **20**):
  - `community` - Checks for CONTRIBUTING and CODE_OF_CONDUCT files
  - `ci` - Checks for CI configuration
  - `changelog` - Checks for a CHANGELOG/NEWS/HISTORY file
  - `citation` - Checks for a CITATION file
  - `python_versions` - Checks `requires-python` excludes EOL Python versions
  - `urls` - Validates project URLs are reachable
  - `doctests` - Runs doctests via `pytest --doctest-modules`
- Profile system with built-in profiles `minimal`, `triage`, `default`,
  `pyopensci`, and `strict`, plus `--profile` and `--list-profiles` CLI options.
  The new `triage` profile runs only the static, no-install/no-network checks —
  intended for CI triage of arbitrary (un-installed) repositories.
- Support for legacy Poetry projects (`[tool.poetry]` with no PEP 621
  `[project]` table) across the metadata, version, python_versions, and
  dependencies checks.
- `structure` now recognizes PEP 420 implicit namespace packages, single-file
  modules, and native-extension build backends (maturin/scikit-build/setuptools-rust).
- GitHub Actions workflows: CI (lint + type-check + pytest matrix on 3.10–3.13)
  and a tag-triggered PyPI release using Trusted Publishing (OIDC).
- `packaging` added as a runtime dependency (accurate PEP 440 version handling).

### Changed

- The `formatting` check now defaults to `tool = "auto"`: it detects ruff vs
  black from `[tool.ruff]`/`ruff.toml`/`[tool.black]` and is **skipped** when no
  formatter is configured (previously hard-coded to ruff).
- Many checks were made more lenient to eliminate false positives on legitimate
  real-world packages: PEP 621 `dynamic` metadata fields, dual-license/suffixed
  license filenames, RST/plain-text README section detection, community files in
  `.github/`/`docs/`, single-module/re-exported `__version__`, dependency extras,
  `pytest` "no tests collected" (exit 5) in doctests, and ruff/mypy
  tool/config-error handling.

### Fixed

- `metadata` no longer reports a false ERROR for packages whose version is
  supplied dynamically (PEP 621 `dynamic = ["version"]`, e.g. setuptools_scm /
  hatch-vcs / uv-dynamic-versioning) or declared under legacy `[tool.poetry]`.

## [0.1.0] - 2024-12-31

### Added

- Initial release of pycmdcheck
- Core `check()` function for programmatic package checking
- CLI with `pycmdcheck` command
- 13 built-in checks:
  - `build` - Verifies package builds (wheel/sdist)
  - `dependencies` - Audits declared vs. actual imports
  - `docs` - Checks for README and docstrings
  - `formatting` - Checks code formatting (ruff format/black)
  - `imports` - Validates imports
  - `license` - Checks for LICENSE file
  - `linting` - Runs ruff/flake8/pylint
  - `metadata` - Validates pyproject.toml
  - `py_typed` - Checks for PEP 561 py.typed marker
  - `structure` - Validates package structure
  - `tests` - Runs pytest/unittest
  - `typing` - Runs mypy/pyright
  - `version` - Verifies version consistency
- Configuration via `[tool.pycmdcheck]` in pyproject.toml
- Plugin system via entry points for custom checks
- Parallel check execution
- Rich terminal output with colors and tables
- JSON output format for CI integration
- `CheckResult`, `CheckStatus`, and `Report` classes
- `BaseCheck` abstract class for custom check development
