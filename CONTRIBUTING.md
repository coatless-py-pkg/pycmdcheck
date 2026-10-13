# Contributing to pycmdcheck

Thank you for helping. Bug reports, fixes, and new checks are all welcome.
Everyone who takes part is expected to follow the
[Code of Conduct](CODE_OF_CONDUCT.md).

To report a problem or suggest a change, open an issue at
<https://github.com/coatless-py-pkg/pycmdcheck/issues>.

## Set up

You need Python 3.11 or newer and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/coatless-py-pkg/pycmdcheck
cd pycmdcheck
uv sync --all-extras --all-groups
```

This creates `.venv/` with the package and every tool used below.

## Check your change

Run these from the top of the repository before you open a pull request.
GitHub runs the same checks on Python 3.11 to 3.14.

```bash
uv run pytest tests src/pycmdcheck --doctest-modules   # run the tests and the doctests
uv run pytest --cov=pycmdcheck       # run the tests and list the lines they missed
uv run ruff check .                  # look for problems in the code
uv run ruff format .                 # format the code
uv run mypy src/pycmdcheck           # check the type hints
uv run pycmdcheck --profile triage   # run the tool on itself
```

The `triage` profile only reads files, so it is the safe way to run pycmdcheck
on its own repository. The `core`, `pyopensci` and `strict` profiles, and a
run with no profile, also run the test suite and build the package from
inside a check. That is slow here, so use `uv run pytest` for the tests
instead.

GitHub also measures coverage, which is the share of the package that the
tests ran. A pull request fails when it falls below 95%. Add
`--cov-report=html` to the coverage command and open `htmlcov/index.html` to
see each file line by line.

## Build the documentation

The site is built with [Quarto](https://quarto.org/docs/get-started/), which
you install on its own. `uv sync --all-extras --all-groups` has already
installed quartodoc.

```bash
cd docs
uv run quartodoc build        # write the API reference to docs/reference/
uv run quartodoc interlinks   # fetch the index of the Python documentation
quarto render                 # write the site to docs/_site/
```

Open `docs/_site/index.html` to read the result. Both `docs/reference/` and
`docs/_site/` are generated, so git does not track them. The pages you edit
are the `.qmd` files in `docs/` and the docstrings in the source.

## Make a release

List each change a user can notice as a bullet under the heading
`# pycmdcheck (development version)` at the top of `CHANGELOG.md`. The
release workflow commits that heading to main after each release. When it is
time to release, one command writes the new version everywhere.

```bash
uvx bump-my-version bump minor   # or patch, or major
```

It changes `pyproject.toml`, the lockfile, the plugin file, the changelog
heading and every pinned command in the README and the docs. Open a pull
request with the result. Once it is merged and main is green, push the tag
`vX.Y.Z`, which publishes the release to PyPI.

If the last job of the release, the one that adds the heading, fails, the
release is already out. Run the failed job again, or start the Development
version workflow by hand.

## Add a check

A new built-in check has to be named in several places. The tests in
`tests/test_docs_sync.py` and `tests/test_profiles.py` compare these lists and
fail until they agree, so a failing test tells you which place is missing.

1. Write the check as a class in `src/pycmdcheck/checks/<name>.py`. It derives
   from `BaseCheck`, sets `name` and `description`, and returns a
   `CheckResult` from `run()`. A short check such as `checks/community.py` is
   a good model.
2. Register the class under `[project.entry-points."pycmdcheck.checks"]` in
   `pyproject.toml`.
3. Add the name to `ALL_CHECKS` in `src/pycmdcheck/profiles.py`.
4. Describe it in `docs/configuration.qmd` with a `### <name>` section and a
   row in the table of statuses. Add the class to the list of built-in checks
   in `docs/_quarto.yml` so it gets a reference page.
5. Add a row to the table of built-in checks in `README.md`.
6. Add tests in `tests/test_check_<name>.py`.

The [custom checks page](docs/custom-checks.qmd) explains what a check should
put in its result and what `run()` must not do.
