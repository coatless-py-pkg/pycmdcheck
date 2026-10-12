<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/coatless-py-pkg/pycmdcheck/main/assets/logo-pycmdcheck-dark-animated.svg">
    <source media="(prefers-color-scheme: light)" srcset="https://raw.githubusercontent.com/coatless-py-pkg/pycmdcheck/main/assets/logo-pycmdcheck-light-animated.svg">
    <img alt="pycmdcheck" src="https://raw.githubusercontent.com/coatless-py-pkg/pycmdcheck/main/assets/logo-pycmdcheck-light-static.svg" width="220">
  </picture>
</p>

<p align="center">A Python package quality checker inspired by R's <code>R CMD check</code>.</p>

One command checks a Python package and says where it falls short. Every
finding has a rule code and a suggestion for what to do about it.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/coatless-py-pkg/pycmdcheck/main/assets/hero-dark.svg">
  <img alt="A submitted package is fetched but never installed. One pycmdcheck command with the triage profile reads its files. The report lists each check with its status, here for pycmdcheck's own repository." src="https://raw.githubusercontent.com/coatless-py-pkg/pycmdcheck/main/assets/hero-light.svg" width="100%">
</picture>

## Installation

```bash
pip install pycmdcheck
```

pycmdcheck needs Python 3.11 or later. Some checks run a tool, and an extra
installs it. The extras are `linting` for ruff, `typing-mypy`,
`typing-pyright`, `build`, and `all`.

```bash
pip install "pycmdcheck[all]"
```

To run one release without installing it, use `uvx`.

```bash
uvx pycmdcheck@0.2.0 --profile triage /path/to/repository
```

## Usage

```bash
pycmdcheck                             # check the current directory
pycmdcheck /path/to/package            # check another directory
pycmdcheck --profile triage            # only read files
pycmdcheck --check metadata,license    # run only these checks
pycmdcheck --skip typing,linting       # leave these checks out
pycmdcheck --verbose                   # show details and suggestions
pycmdcheck --json                      # a report for programs
pycmdcheck --format markdown --output report.md   # a report for a comment
```

A run with no profile also runs the project's tests, doctests and build. For
a repository you do not trust, use `--profile triage`, which installs
nothing, starts no tool and makes no network request.

The [getting started guide](https://py-pkg.thecoatlessprofessor.com/pycmdcheck/getting-started.html)
walks through a first run, and the
[command line reference](https://py-pkg.thecoatlessprofessor.com/pycmdcheck/cli.html)
describes every option.

## Checks

| Check | What it does |
|-------|--------------|
| `build` | Builds the package and checks that a wheel or source archive comes out |
| `changelog` | Looks for a changelog or news file |
| `ci` | Looks for continuous integration configuration |
| `citation` | Looks for a `CITATION` file or other citation metadata |
| `community` | Looks for a contributing guide and a code of conduct |
| `dependencies` | Compares the declared dependencies with the imports in the code |
| `docs` | Looks for a README, and on request for a docs site and docstrings |
| `doctests` | Runs the examples in docstrings |
| `formatting` | Runs the formatter the project has configured, ruff or black |
| `license` | Looks for a license file and reports the license type |
| `linting` | Runs the linter the project has configured, ruff, flake8 or pylint |
| `metadata` | Checks the `[project]` table of `pyproject.toml` |
| `py_typed` | Looks for the `py.typed` marker of a typed package |
| `python_versions` | Reports a `requires-python` that still allows end-of-life Python versions |
| `structure` | Looks for an importable package or module |
| `test_suite` | Looks for tests without running them |
| `tests` | Runs the tests with pytest or unittest |
| `typing` | Runs the type checker the project has configured, mypy or pyright |
| `urls` | Requests each project URL, which needs network access |
| `version` | Compares `__version__` in the code with the version in `pyproject.toml` |

`pycmdcheck --list` prints the installed checks, and `pycmdcheck --list-rules`
prints every rule with its suggestion. The
[rules page](https://py-pkg.thecoatlessprofessor.com/pycmdcheck/rules.html)
has the same list.

## Profiles

A profile runs a set of checks and sets some of their options.

| Profile | Runs |
|---------|------|
| `minimal` | `metadata`, `structure` and `license` |
| `triage` | The checks that only read files |
| `core` | Metadata, layout, license, docs and the code-quality tools |
| `pyopensci` | Every built-in check, set up for pyOpenSci's requirements |
| `strict` | Every built-in check, with ruff, strict mypy and a higher docstring minimum |

```bash
pycmdcheck --profile pyopensci
pycmdcheck --list-profiles    # the checks and options of each profile
```

## Configuration

Settings go in the `pyproject.toml` of the package being checked.

```toml
[tool.pycmdcheck]
fail_on = ["warning"]    # exit with code 1 on warnings as well as errors

[tool.pycmdcheck.checks]
urls = false             # turn a check off
tests = { runner = "pytest", timeout = 600 }
```

The [configuration reference](https://py-pkg.thecoatlessprofessor.com/pycmdcheck/configuration.html)
lists the options of every check.

## Python API

```python
from pycmdcheck import check

report = check("/path/to/package", profile="triage")
for result in report.results:
    print(result.status, result.name, result.message)
```

## Learn more

- [Checking a repository you do not own](https://py-pkg.thecoatlessprofessor.com/pycmdcheck/triage.html),
  for bots and reviewers, with `--config` and the Markdown report
- [Writing a custom check](https://py-pkg.thecoatlessprofessor.com/pycmdcheck/custom-checks.html)
- [Working with an AI assistant](https://py-pkg.thecoatlessprofessor.com/pycmdcheck/assistants.html),
  which starts with `pycmdcheck --install-skill`
- [Changelog](https://py-pkg.thecoatlessprofessor.com/pycmdcheck/changelog.html)

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | No check reached the `--fail-on` status, which is `error` by default |
| 1 | A check reached it |
| 2 | Usage error, such as an unknown check name |
| 3 | pycmdcheck itself failed |

## License

MIT
