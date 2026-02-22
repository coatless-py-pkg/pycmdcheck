# Changelog

All notable changes to pycmdcheck will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
