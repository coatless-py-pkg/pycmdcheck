# pycmdcheck

A Python package quality checker inspired by R's `R CMD check`.

## Installation

```bash
pip install pycmdcheck
```

Or with uv:

```bash
uv add pycmdcheck
```

### Optional dependencies

```bash
# For mypy type checking
pip install pycmdcheck[typing-mypy]

# For pyright type checking
pip install pycmdcheck[typing-pyright]

# For ruff linting
pip install pycmdcheck[linting]

# All optional dependencies
pip install pycmdcheck[all]
```

## Usage

### Command Line

```bash
# Check current directory
pycmdcheck

# Check a specific directory
pycmdcheck /path/to/package

# Run only specific checks
pycmdcheck -c metadata -c tests

# Skip specific checks
pycmdcheck -s typing -s linting

# Fail on warnings too
pycmdcheck --fail-on error --fail-on warning

# List available checks
pycmdcheck --list

# JSON output
pycmdcheck --json

# Stop after first error (fail fast)
pycmdcheck -x
pycmdcheck --fail-fast

# Verbose output with details
pycmdcheck -v

# Use a check profile
pycmdcheck --profile pyopensci
pycmdcheck --profile minimal
pycmdcheck --profile strict

# List available profiles
pycmdcheck --list-profiles
```

### With uv

```bash
uv run pycmdcheck
uv run pycmdcheck -c tests -c linting
```

### Python API

```python
from pycmdcheck import check, CheckStatus

# Check current directory
report = check(".")

# Check specific directory
report = check("/path/to/package")

# Run only specific checks
report = check(".", checks=["metadata", "tests"])

# Skip specific checks
report = check(".", skip=["linting", "typing"])

# Check results
if report.passed:
    print("All checks passed!")
else:
    for result in report.results:
        print(f"{result.status}: {result.name} - {result.message}")

# Get counts by status
counts = report.count_by_status()
print(f"OK: {counts[CheckStatus.OK]}, Errors: {counts[CheckStatus.ERROR]}")

# JSON output
import json
print(json.dumps(report.to_dict(), indent=2))
```

## Built-in Checks

### Core checks

| Check | Description |
|-------|-------------|
| `build` | Verifies the package builds successfully (wheel/sdist) |
| `dependencies` | Audits declared vs. actual imports, warns on unbounded versions |
| `docs` | Checks for README (content sections, word count) and docstrings |
| `formatting` | Checks code formatting (ruff format/black) |
| `imports` | Validates all imports can be resolved |
| `license` | Checks for LICENSE file and OSI-approved license validation |
| `linting` | Runs ruff, flake8, or pylint |
| `metadata` | Validates pyproject.toml fields (required, recommended, extended) |
| `py_typed` | Checks for PEP 561 py.typed marker |
| `structure` | Checks package directory structure (src or flat layout) |
| `tests` | Runs pytest or unittest and reports results |
| `typing` | Runs mypy or pyright type checker |
| `version` | Verifies version consistency between pyproject.toml and code |

### pyOpenSci / community checks

| Check | Description |
|-------|-------------|
| `changelog` | Checks for CHANGELOG, NEWS, or HISTORY file |
| `ci` | Checks for CI configuration (GitHub Actions, CircleCI, etc.) |
| `citation` | Checks for CITATION.cff or CITATION.bib file |
| `community` | Checks for CONTRIBUTING.md and CODE_OF_CONDUCT.md |
| `doctests` | Runs doctests via pytest --doctest-modules |
| `python_versions` | Checks requires-python excludes EOL Python versions |
| `urls` | Validates project URLs in metadata are reachable |

## Profiles

Profiles bundle checks for common use cases:

| Profile | Description | Checks |
|---------|-------------|--------|
| `minimal` | Quick sanity checks | metadata, structure, license |
| `triage` | Static checks only — no install, external tools, or network (for CI triage of arbitrary repos) | All static checks (excludes tests, imports, dependencies, build, linting, typing, formatting, doctests, urls) |
| `default` | Standard quality checks | All original core checks |
| `pyopensci` | pyOpenSci onboarding | All checks with stricter docs settings |
| `strict` | Maximum strictness | All checks with strict typing |

```bash
# Run pyOpenSci onboarding checks
pycmdcheck --profile pyopensci

# Static-only triage (safe to run on an un-installed repo in CI)
pycmdcheck --profile triage

# Quick check before committing
pycmdcheck --profile minimal
```

Profiles can be combined with `--skip` to exclude specific checks:

```bash
pycmdcheck --profile pyopensci -s urls -s doctests
```

## Configuration

Configure pycmdcheck in your `pyproject.toml`:

```toml
[tool.pycmdcheck]
# Fail on these statuses (default: ["error"])
fail_on = ["error", "warning"]

[tool.pycmdcheck.checks]
# Enable/disable checks with boolean
metadata = true
structure = true
imports = true
license = true

# Configure checks with options
tests = { enabled = true, runner = "pytest" }
linting = { enabled = true, tool = "ruff" }
typing = { enabled = true, tool = "mypy", strict = false }
# formatting "tool" defaults to "auto": detects ruff vs black from
# [tool.ruff]/ruff.toml or [tool.black]; skips if neither is configured.
formatting = { enabled = true, tool = "auto" }
docs = { enabled = true, require_readme = true, check_docstrings = false, check_readme_sections = false }

# New checks (disabled by default in "default" profile)
community = true
ci = true
changelog = true
citation = true
python_versions = true
urls = { enabled = true, timeout = 10 }
doctests = true
```

### Linting tools

Supported linting tools:
- `ruff` (default, recommended)
- `flake8`
- `pylint`

```toml
[tool.pycmdcheck.checks.linting]
tool = "ruff"
args = ["--select=E,F,W"]
```

### Build options

```toml
[tool.pycmdcheck.checks.build]
timeout = 300  # seconds (default: 120)
args = ["--wheel"]
```

### Type checking tools

Supported type checkers:
- `mypy` (default)
- `pyright`

```toml
[tool.pycmdcheck.checks.typing]
tool = "pyright"
strict = true
```

## Custom Checks

Create custom checks by implementing the `Check` protocol:

```python
from pathlib import Path
from typing import Any
from pycmdcheck.checks.base import BaseCheck
from pycmdcheck.results import CheckResult, CheckStatus

class MyCustomCheck(BaseCheck):
    name = "my_custom_check"
    description = "My custom quality check"

    def run(self, package_path: Path, config: dict[str, Any]) -> CheckResult:
        # Your check logic here
        issues = []  # ... populate with any issues found
        if not issues:
            return CheckResult(
                name=self.name,
                status=CheckStatus.OK,
                message="All good!",
            )
        return CheckResult(
            name=self.name,
            status=CheckStatus.ERROR,
            message="Something is wrong",
            details=issues,
        )
```

Register your check via entry points in `pyproject.toml`:

```toml
[project.entry-points."pycmdcheck.checks"]
my_custom_check = "my_package.checks:MyCustomCheck"
```

## Check Statuses

| Status | Symbol | Description |
|--------|--------|-------------|
| OK | ✓ | Check passed |
| NOTE | ℹ | Minor observation, not a problem |
| WARNING | ⚠ | Potential issue, should be reviewed |
| ERROR | ✗ | Check failed |
| SKIPPED | ○ | Check was skipped (e.g., tool not installed) |

## License

MIT
