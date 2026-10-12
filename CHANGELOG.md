# pycmdcheck 0.2.0

This release raises fewer false alarms and explains every finding.

## New features

- Every finding now has a rule code, such as `license.missing`, and a
  one-sentence suggestion that says what to do about it. `--verbose` shows
  the suggestions, `--list-rules` lists every rule, and the JSON report
  holds both under `findings` in each result.

- `--format markdown` writes a report made for a pull request or issue
  comment, and `--output FILE` saves any report to a file.

  ```bash
  pycmdcheck --profile triage --format markdown --output report.md
  ```

- New `test_suite` check says whether a project has tests without running
  them, so the `triage` profile can now report a missing test suite.

- New `--config FILE` option takes the profile, the checks and their options
  from a settings file kept outside the checked project. It is for bots and
  organizations that check repositories they do not own. The
  `[tool.pycmdcheck]` table of the checked project is then not read at all.

- `--profile triage` now gives the same results whatever the checked
  project's `[tool.pycmdcheck.checks]` table says. Its checks only read
  files, and the profile sets every option they use.

- Skipped checks now say why. Each one has a `skip_reason`, such as
  `tool-missing`, `not-configured` or `not-applicable`, and never fails a
  run.

- New Agent Skill teaches an AI assistant to run pycmdcheck and to fix each
  finding by its rule code and suggestion. `pycmdcheck --install-skill`
  copies it into `.claude/skills`, and the repository also works as a Claude
  Code plugin.

- `--check` and `--skip` now take several names separated by commas, as in
  `--check tests,linting`.

- `--list-profiles` now shows the checks and options of each profile, and
  `--list`, `--list-profiles` and `--list-rules` take `--json`.

- The JSON report gains `schema_version`, `pycmdcheck_version`, `profile`,
  `config_file`, `fail_on`, `failed`, `requested_checks` and
  `skipped_checks`, and every result gains `findings` and `skip_reason`. A
  program that compares the exact set of keys needs an update.

- Exit code 3 now marks a failure of pycmdcheck itself, so a caller can tell
  it from a report of findings.

- The Python API gains `pycmdcheck.Finding`, `BaseCheck.rules`,
  `BaseCheck.hints` and `CheckResult.skip()` for custom checks that report
  findings, and a `profile=` argument for `check()` and `run_checks()`. A
  custom check that reports no findings still works.

## Breaking changes

- Python 3.10 is no longer supported. pycmdcheck needs Python 3.11 or later.

- Many results change status. A check with nothing to look at is now
  skipped and no longer gives a note, a warning or an error, and each cause
  is reported by one check. A project with no tests, for example, now gets
  its warning from `test_suite` and a skipped `tests`.

- `linting` and `typing` now run only when the project has configured a
  tool, and are skipped otherwise. To run one anyway, name the tool, as in
  `linting = { tool = "ruff" }` under `[tool.pycmdcheck.checks]`.

- `formatting` no longer takes a `[tool.ruff]` table alone as the sign of a
  ruff formatter, so such a project is now skipped. Add a
  `[tool.ruff.format]` table, which may be empty, to keep the check.

- `--fail-on` now names a threshold, so `--fail-on warning` also fails on an
  error. `fail_on` in `[tool.pycmdcheck]` now takes effect when `--fail-on`
  is not given, under every profile, so a project that sets
  `fail_on = ["warning"]` can now get exit code 1. Pass `--fail-on` or use
  `--config` when you check repositories you do not own.

- `doctests` now gives an error for a failed docstring example, and the run
  exits with code 1. Fix the example, or mark it with `# doctest: +SKIP`.

- `dependencies` now warns about an undeclared import also when the module
  is installed, and when the project declares no dependencies at all. Add
  the package to `dependencies` in the `[project]` table.

- `--skip` now gives a usage error with exit code 2 when it names an unknown
  check, holds an empty name, or leaves no check to run. So does a `PATH`
  that is a file.

- `--profile default` and the `imports` check are deprecated. Use
  `--profile core`, which runs the same checks, and `--check dependencies`,
  which now does the work of `imports`. Take `imports` out of `--skip`.

- `--profile strict` now runs ruff and mypy whatever tool the project names
  in `[tool.pycmdcheck.checks]`, such as flake8 or pyright. Use
  `--profile pyopensci` to keep the project's choice.

- The `all` extra now installs the `build` package, as the new `build` extra
  does, so the `build` check builds the project where it was skipped. Pass
  `--skip build` to leave it out.

- Report text is reworded. The closing line starts with `Failed:` after a
  failed run, many messages and detail lines changed, and advice moved from
  the detail lines to the suggestion. A program should read the JSON report
  and match the `code` of a finding.

- A check that raises an exception no longer prints its traceback. Run with
  `--debug` to see it.

- The `dev` and `docs` extras are gone. Both are now dependency groups for
  people who work on pycmdcheck.

- `check()` and `run_checks()` now raise `FileNotFoundError` or
  `NotADirectoryError` for a path that is not a directory, and `ValueError`
  for an unknown profile or an unknown name in `checks=`.

- A plugin can no longer replace a built-in check by registering under its
  name.

- For authors of custom checks, the class attributes `CHANGELOG_FILENAMES`,
  `CITATION_FILENAMES`, `CONTRIBUTING_FILENAMES` and
  `CODE_OF_CONDUCT_FILENAMES` give way to `CHANGELOG_STEMS`,
  `CITATION_EXTENSIONS`, `CONTRIBUTING_STEMS` and `CODE_OF_CONDUCT_STEMS`.
  Several undocumented helpers of `pycmdcheck.package_layout` are gone too.

## Minor improvements and fixes

- `--profile pyopensci` and `--profile strict` now set the minimum docstring
  coverage to 50% and 80%, so missing docstrings give a note only below that
  coverage.

- `build` now builds a project that has only a `setup.py`, and a `build.py`
  or `build/` in the project no longer gets in its way.

- `changelog`, `ci`, `citation` and `community` now accept many more file
  names and services, such as `RELEASE_NOTES`, a directory of news
  fragments, Forgejo Actions, `CITATION.rst`, `codemeta.json` and `CONDUCT`.

- `changelog`, `ci`, `citation`, `community` and `docs` now match file names
  in any letter case, so results agree between macOS and Linux.

- `dependencies` no longer flags guarded imports, such as those under
  `if TYPE_CHECKING` or in a `try` that catches `ImportError`. It also
  leaves out the standard library, the project's own modules, and code in
  tests and documentation.

- `docs` gains a `check_docs_site` option, which gives a note when a project
  has no documentation beyond its README. The `triage`, `pyopensci` and
  `strict` profiles turn it on.

- `docs` now finds a README in `.github/` or `docs/`, or one declared in
  `pyproject.toml`.

- `doctests` now names each failed example and gives the line and the
  reason, as in `line 29: expected 10, got 9`.

- `doctests` now collects only the project's own packages, and still runs
  the other examples when one file cannot be imported.

- `license` now gives a note when the declared license expression names no
  license approved by the Open Source Initiative. The approved licenses come
  from the SPDX license list.

- `license` now accepts `LICENSE/` and `LICENSES/` directories, `UNLICENSE`,
  and files that are not UTF-8. It no longer labels MPL, LGPL, AGPL or EPL
  texts as GPL.

- `linting` no longer rewrites files when the project's ruff settings turn
  `fix` on, and lists each finding as `path:row:col CODE message`.

- `linting` and `formatting` now find ruff and black when they are installed
  next to pycmdcheck, which is where `pipx` and `uv tool` put them.

- `metadata` now flags values that cannot be right. An empty `name` or
  `version` gives an error, and a setuptools project that lists a
  `License ::` classifier beside a license expression gets a warning,
  because setuptools 77 and later refuses to build it.

- `metadata` now gives a warning, not an error, when `setup.py` or
  `setup.cfg` holds the metadata beside a `pyproject.toml` that has no
  `[project]` table.

- `py_typed` now suggests the `py.typed` marker only for a package that is
  clearly typed, and counts `.pyi` stub files.

- `python_versions` now reads `python_requires` from `setup.py`, and knows
  end-of-life dates through Python 3.15.

- `structure` now finds a package beside `src/`, namespace packages,
  stub-only packages and packages with underscore names. It no longer warns
  about Rust, C or front-end directories under `src/`, and it recognizes
  native build tools named under `[build-system] requires`, such as
  setuptools-rust, Cython, pybind11 and cffi.

- `tests` now finds tests inside the package, under `testpaths`, and in
  `*_test.py` files. It says "Tests could not be collected" when a test
  module cannot be imported.

- `typing` now gives ok when mypy prints only notes, counts the warnings of
  pyright apart from its errors, and no longer passes `--strict` to pyright.

- `urls` no longer contacts local or private hosts, and follows a redirect
  only to another `http` or `https` address.

- `urls` now gives a note, not a warning, for an address it could not
  verify, such as one that answers HTTP 403 or times out. Only 404, 410 and
  an unknown host give a warning.

- `version` now treats `1.0` and `1.0.0` as the same version, as PEP 440
  does.

- A `pyproject.toml` that cannot be parsed no longer ends the run with a
  traceback. `metadata` reports the file, and the checks that need it are
  skipped.

- A tool that is installed but cannot run now gives a warning that names it,
  such as "ruff could not run", not an error.

- Files in the checked repository are now read only when they are regular
  files, and only up to a size limit.

- `python -m pycmdcheck` now removes the current directory from the module
  search path, so a repository checked from its own root cannot register
  checks of its own.

- The documentation gains pages on the rules, on triage and on working with
  an AI assistant, and is published at
  <https://py-pkg.thecoatlessprofessor.com/pycmdcheck/>.

# pycmdcheck 0.1.1

- `--exit-zero` always exits with code 0, whatever the checks find, so the
  exit code says whether pycmdcheck ran. It is for automation that reads the
  results itself, for example from `--json`.

- Project URLs now point to the repository at
  `github.com/coatless-py-pkg/pycmdcheck`.

- Installation instructions now use `pip install pycmdcheck`.

- Docstrings of the Python API now match what the functions return, and
  their examples run under `pytest --doctest-modules`.

# pycmdcheck 0.1.0

First release on PyPI.

- `check()` and the `pycmdcheck` command check the quality of a Python
  package, with a text report for the terminal and a JSON report for
  automation.

- Built-in checks `metadata`, `structure`, `tests`, `linting`, `typing`,
  `imports`, `license`, `docs`, `dependencies`, `build`, `formatting`,
  `version`, `py_typed`, `community`, `ci`, `changelog`, `citation`,
  `python_versions`, `urls` and `doctests`.

- `--profile` runs a set of checks, and `--list-profiles` lists the sets,
  which are `minimal`, `triage`, `default`, `pyopensci` and `strict`. The
  `triage` profile only reads files, so it can check a repository that is
  not installed.

- `[tool.pycmdcheck]` in `pyproject.toml` configures the checks, and custom
  checks plug in through entry points.

- `metadata`, `version`, `python_versions` and `dependencies` read legacy
  Poetry projects, which have a `[tool.poetry]` table and no `[project]`
  table.

- `structure` recognizes namespace packages, single-file modules, and the
  native build backends maturin and scikit-build.

- `formatting` finds the configured formatter, ruff or black, and is skipped
  when none is configured.

- The Python API exports `CheckResult`, `CheckStatus`, `Report` and
  `BaseCheck` for custom checks.
