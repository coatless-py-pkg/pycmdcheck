"""Tests for the docs check: README detection, README topics, docstring coverage."""

import os
from pathlib import Path
from typing import Any

import pytest

from pycmdcheck.checks.docs import DocsCheck
from pycmdcheck.checks.metadata import MetadataCheck
from pycmdcheck.pyproject_reader import clear_cache
from pycmdcheck.results import CheckResult, CheckStatus, Finding
from pycmdcheck.runner import fill_hints
from tests.helpers import found, write_files

# 80 words, none of which is an installation or usage keyword.
LONG = "# My Package\n\n" + "A small library that validates Python projects. " * 11
INSTALL = "\n## Installation\n\n    pip install mypkg\n"
USAGE = "\n## Usage\n\n    import mypkg\n"
NOT_FOUND = "No README file found in the project root, .github/ or docs/"
# What is said of a README that could not be read.
NOT_CHECKED = "The length of the README was not checked"
# How the detail line ends when not every project link could be read.
NO_LINK_SEARCH = ", so a documentation link could not be looked for"
OK = (CheckStatus.OK, "Documentation present")
NOTE = (CheckStatus.NOTE, "Some documentation improvements suggested")


def project(extra: str = "", name: str = "mypkg") -> str:
    """Return a pyproject.toml, with *extra* lines in the [project] table."""
    return f'[project]\nname = "{name}"\nversion = "0.1.0"\n{extra}'


def run(root: Path, files: dict[str, str | bytes], **config: Any) -> CheckResult:
    """Write *files* under *root* and run the docs check there."""
    write_files(root, files)
    return DocsCheck().run(root, config)


MISSING_README = ("docs.missing-readme", CheckStatus.WARNING)
UNREADABLE_README = ("docs.unreadable-readme", CheckStatus.WARNING)
SHORT_README = ("docs.readme-too-short", CheckStatus.NOTE)
MISSING_DOCSTRINGS = ("docs.missing-docstrings", CheckStatus.NOTE)
NOT_COUNTED = ("docs.docstrings-not-counted", CheckStatus.NOTE)


def test_rules_list_every_code() -> None:
    assert sorted(DocsCheck.rules) == [
        "docs.docstrings-not-counted",
        "docs.missing-docstrings",
        "docs.missing-installation",
        "docs.missing-readme",
        "docs.missing-site",
        "docs.missing-usage",
        "docs.readme-too-short",
        "docs.unreadable-readme",
    ]


class TestReadme:
    """Finding and sizing up the README."""

    @pytest.mark.parametrize(
        "name",
        [
            "README.md",
            "README.rst",
            "README.txt",
            "README",
            "Readme.MD",
            "README.adoc",
            ".github/README.md",
            "docs/README.rst",
        ],
    )
    def test_readme_names(self, tmp_path: Path, name: str) -> None:
        result = run(tmp_path, {name: LONG})
        assert (result.status, result.message) == OK
        assert result.details == [f"Found README: {name}", "README has 80 words"]
        assert (result.findings, result.skip_reason) == ([], None)

    @pytest.mark.parametrize(
        "declared",
        [
            'readme = "docs/intro.md"',
            'readme = {file = "docs/intro.md", content-type = "text/markdown"}',
        ],
    )
    def test_declared_readme_file(self, tmp_path: Path, declared: str) -> None:
        files = {"pyproject.toml": project(declared), "docs/intro.md": LONG}
        result = run(tmp_path, files)
        assert (result.status, result.message) == OK
        assert result.details[0] == "Found README: docs/intro.md"

    @pytest.mark.parametrize(
        ("declared", "detail"),
        [
            (
                f'readme = {{text = "{LONG[14:]}", content-type = "text/plain"}}',
                "README text is declared inline in pyproject.toml",
            ),
            (
                'dynamic = ["readme"]',
                "README is declared as dynamic in pyproject.toml",
            ),
        ],
        ids=["inline text", "dynamic"],
    )
    def test_readme_declared_without_a_file(
        self, tmp_path: Path, declared: str, detail: str
    ) -> None:
        result = run(tmp_path, {"pyproject.toml": project(declared)})
        assert (result.status, result.message) == OK
        assert result.details[0] == detail

    @pytest.mark.parametrize(
        ("files", "declared"),
        [
            ({}, None),
            ({"README/notes.txt": LONG}, None),
            ({"pyproject.toml": project('readme = "../README.md"')}, "../README.md"),
            ({"pyproject.toml": project('readme = "NOPE.md"')}, "NOPE.md"),
        ],
        ids=["nothing", "a directory named README", "declared outside", "declared"],
    )
    def test_missing_readme_warns_and_says_where_it_looked(
        self, tmp_path: Path, files: dict[str, str | bytes], declared: str | None
    ) -> None:
        result = run(tmp_path, files)
        assert result.status == CheckStatus.WARNING
        assert result.message == "Documentation incomplete"
        unused = (
            f'pyproject.toml declares readme = "{declared}",'
            " which was not found inside the project"
        )
        assert result.details == ([unused] if declared else []) + [NOT_FOUND]
        assert found(result) == [MISSING_README]
        assert result.findings[0].message == NOT_FOUND

    @pytest.mark.parametrize(
        ("config", "unchecked"),
        [
            ({}, NOT_CHECKED),
            ({"require_readme": False}, NOT_CHECKED),
            (
                {"check_readme_sections": True},
                NOT_CHECKED + ", and installation and usage text was not looked for",
            ),
        ],
        ids=["defaults", "none required", "topics asked for"],
    )
    def test_unreadable_readme_warns(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        config: dict[str, Any],
        unchecked: str,
    ) -> None:
        """A README that cannot be opened is a WARNING that gives the reason."""

        def refuse(path: Path) -> str:
            raise PermissionError(13, "Permission denied", str(path))

        monkeypatch.setattr("pycmdcheck.checks.docs.read_text_capped", refuse)
        result = run(tmp_path, {"docs/README.rst": LONG}, **config)
        assert result.status == CheckStatus.WARNING
        assert result.message == "Documentation incomplete"
        said = "Could not read docs/README.rst: Permission denied"
        assert result.details == ["Found README: docs/README.rst", said, unchecked]
        # The command in the hint can be typed as it is shown.
        hint = (
            "Give yourself read permission on the README, for example with "
            "chmod u+r docs/README.rst."
        )
        assert result.findings == [
            Finding("docs.unreadable-readme", said, CheckStatus.WARNING, hint)
        ]
        # The hint the rule carries is the same sentence, for README.md.
        named = hint.replace("docs/README.rst", "README.md")
        assert named == DocsCheck.hints["docs.unreadable-readme"]

    def test_named_readme_wins_over_a_bare_stub(self, tmp_path: Path) -> None:
        result = run(tmp_path, {"README": "See README.md", "README.md": LONG})
        assert (result.status, result.message) == OK
        assert result.details == ["Found README: README.md", "README has 80 words"]

    def test_readme_filenames_attribute_sets_the_preference(
        self, tmp_path: Path
    ) -> None:
        names = ["README.md", "README.rst", "README.txt", "README"]
        assert DocsCheck.README_FILENAMES == names

        class Spanish(DocsCheck):
            README_FILENAMES = ["LEEME.md"]

        (tmp_path / "LEEME.md").write_text(LONG, encoding="utf-8")
        assert Spanish().run(tmp_path, {}).details[0] == "Found README: LEEME.md"

    @pytest.mark.parametrize(
        ("config", "details"),
        [
            ({}, []),
            (
                {"check_docstrings": True},
                ["No public items found for the docstring check"],
            ),
        ],
        ids=["defaults", "docstring scan with nothing to scan"],
    )
    def test_no_readme_and_none_required_is_skipped(
        self, tmp_path: Path, config: dict[str, Any], details: list[str]
    ) -> None:
        """With no README to read and no docstring to count, nothing was checked."""
        result = run(tmp_path, {}, require_readme=False, **config)
        assert result.status == CheckStatus.SKIPPED
        assert result.message == "No documentation to check"
        assert result.skip_reason == "not-applicable"
        assert result.details == [
            *details,
            "No README file found, and require_readme is false",
        ]
        assert result.findings == []

    @pytest.mark.parametrize(
        ("words", "status", "detail", "codes"),
        [
            (49, NOTE, "README is very short (< 50 words)", [SHORT_README]),
            (50, OK, "README has 50 words", []),
        ],
    )
    def test_word_count_boundary(
        self,
        tmp_path: Path,
        words: int,
        status: tuple[Any, str],
        detail: str,
        codes: list[tuple[str, CheckStatus]],
    ) -> None:
        result = run(tmp_path, {"README.md": "word " * words})
        assert (result.status, result.message) == status
        assert result.details == ["Found README: README.md", detail]
        assert found(result) == codes

    @pytest.mark.parametrize(
        "content",
        ["这是一个用于验证项目结构的库。" * 20, ("café " * 60).encode("latin-1")],
        ids=["no spaces between words", "latin-1"],
    )
    def test_readme_is_graded_on_content_not_encoding(
        self, tmp_path: Path, content: str | bytes
    ) -> None:
        result = run(tmp_path, {"README.md": content})
        assert (result.status, result.message) == OK


class TestReadmeTopics:
    """The opt-in search for installation and usage text."""

    @pytest.mark.parametrize(
        ("body", "missing"),
        [
            (INSTALL + USAGE, []),
            ("", ["Installation", "Usage"]),
            (INSTALL + "\n```python\n>>> import requests\n```\n", []),
            (INSTALL + "\n.. code:: python\n\n    from mypkg import validate\n", []),
            ("\n## Setup\n\n    uv add mypkg\n\n## Demo\n", []),
            ("\n    $ pip install mypkg\n", ["Usage"]),
            (
                "\nBuilt with setuptools. Importing data is fast.\n",
                ["Installation", "Usage"],
            ),
            (INSTALL + "\nThis outlines how to contribute.\n", ["Usage"]),
        ],
        ids=[
            "headings",
            "neither",
            "doctest block",
            "import in an RST block",
            "setup, uv add and demo",
            "shell prompt alone",
            "keywords inside other words",
            "how to contribute",
        ],
    )
    def test_topics(self, tmp_path: Path, body: str, missing: list[str]) -> None:
        result = run(tmp_path, {"README.rst": LONG + body}, check_readme_sections=True)
        assert (result.status, result.message) == (NOTE if missing else OK)
        assert result.details[2:] == [
            f"README does not appear to mention: {topic}" for topic in missing
        ]
        assert found(result) == [
            (f"docs.missing-{topic.lower()}", CheckStatus.NOTE) for topic in missing
        ]
        assert [finding.message for finding in result.findings] == result.details[2:]
        assert {code for code, _ in found(result)} <= set(DocsCheck.rules)


MISSING_SITE = ("docs.missing-site", CheckStatus.NOTE)
SITE_LINK = "https://mypkg.example.org"


class TestDocsSite:
    """Documentation beyond the README, looked for when check_docs_site is on."""

    def site(self, root: Path, files: dict[str, str | bytes]) -> CheckResult:
        return run(root, {"README.md": LONG, **files}, check_docs_site=True)

    def test_nothing_but_a_readme_is_a_note(self, tmp_path: Path) -> None:
        result = self.site(tmp_path, {"pyproject.toml": project()})
        assert (result.status, result.message) == NOTE
        assert found(result) == [MISSING_SITE]
        assert result.findings[0].message == "No documentation beyond the README found"
        assert "docs/, doc/ or documentation/" in result.details[-1]

    @pytest.mark.parametrize(
        "relative",
        [
            "docs/conf.py",
            "docs/source/conf.py",
            "doc/conf.py",
            "mkdocs.yml",
            "mkdocs.yaml",
            "docs/mkdocs.yml",
            "_quarto.yml",
            "docs/_quarto.yml",
            "docs/_toc.yml",
            "myst.yml",
            "zensical.toml",
            ".readthedocs.yaml",
            ".readthedocs.yml",
            "docs/index.md",
            "docs/tutorial.ipynb",
            "documentation/guide.rst",
            "docs/guide/intro.md",
            "doc/tutorials/basics/first.ipynb",
        ],
    )
    def test_a_site_setup_or_a_page_is_enough(
        self, tmp_path: Path, relative: str
    ) -> None:
        result = self.site(tmp_path, {"pyproject.toml": project(), relative: "x\n"})
        assert (result.status, result.message) == OK
        assert result.details[-1] == f"Found documentation: {relative}"

    @pytest.mark.parametrize(
        "relative",
        [
            "docs/README.md",
            "docs/CONTRIBUTING.md",
            "docs/CODE_OF_CONDUCT.md",
            "docs/PULL_REQUEST_TEMPLATE.md",
            "docs/ISSUE_TEMPLATE/bug_report.md",
            "docs/requirements.txt",
            "conf.py",
        ],
    )
    def test_what_does_not_count_as_documentation(
        self, tmp_path: Path, relative: str
    ) -> None:
        result = self.site(tmp_path, {"pyproject.toml": project(), relative: "x\n"})
        assert found(result) == [MISSING_SITE]

    @pytest.mark.parametrize(
        ("urls", "linked"),
        [
            (f'Documentation = "{SITE_LINK}"', "Documentation"),
            (f'docs = "{SITE_LINK}"', "docs"),
            (f'"API Docs" = "{SITE_LINK}"', "API Docs"),
            ('Homepage = "https://mypkg.readthedocs.io/en/latest/"', "Homepage"),
            ('Homepage = "https://someone.github.io/mypkg"', "Homepage"),
            ('Homepage = "https://github.com/someone/mypkg"', None),
            ('Docker = "https://hub.docker.com/r/someone/mypkg"', None),
            ('Source = "https://github.com/someone/readthedocs.io"', None),
            # A documentation label that only leads back to the README.
            ('Documentation = "https://github.com/someone/mypkg#readme"', None),
            ('Documentation = "https://github.com/someone/mypkg"', None),
            (
                'Documentation = "https://github.com/someone/mypkg/blob/main/README.md"',
                None,
            ),
            ('Documentation = "https://gitlab.com/someone/mypkg/"', None),
            (
                'Documentation = "https://github.com/someone/mypkg/wiki"',
                "Documentation",
            ),
            (
                'Docs = "https://github.com/someone/mypkg/tree/main/docs"',
                "Docs",
            ),
            # An address that cannot be split into its parts.
            ('Documentation = "https://[mypkg"', None),
        ],
    )
    def test_a_documentation_link_in_the_metadata(
        self, tmp_path: Path, urls: str, linked: str | None
    ) -> None:
        text = project() + f"[project.urls]\n{urls}\n"
        result = self.site(tmp_path, {"pyproject.toml": text})
        if linked is None:
            assert found(result) == [MISSING_SITE]
        else:
            assert (result.status, result.message) == OK
            assert result.details[-1] == f"Found documentation: {linked} link"

    @pytest.mark.parametrize(
        ("files", "label"),
        [
            (
                {
                    "pyproject.toml": '[tool.poetry]\nname = "mypkg"\nversion = "1"\n'
                    f'documentation = "{SITE_LINK}"\n'
                },
                "Documentation",
            ),
            (
                {
                    "setup.cfg": "[metadata]\nname = mypkg\nproject_urls =\n"
                    f"    Source = https://github.com/someone/mypkg\n"
                    f"    Documentation = {SITE_LINK}\n"
                },
                "Documentation",
            ),
            (
                {
                    "setup.py": "from setuptools import setup\n"
                    'setup(name="x", '
                    f'project_urls={{"Documentation": "{SITE_LINK}"}})\n'
                },
                "Documentation",
            ),
            # The url of setup.cfg or setup.py is the Homepage link.
            (
                {
                    "setup.cfg": "[metadata]\nname = mypkg\n"
                    "url = https://mypkg.readthedocs.io\n"
                },
                "Homepage",
            ),
            (
                {
                    "setup.py": "from setuptools import setup\n"
                    'setup(name="x", url="https://mypkg.readthedocs.io")\n'
                },
                "Homepage",
            ),
        ],
        ids=["poetry", "setup.cfg", "setup.py", "setup.cfg url", "setup.py url"],
    )
    def test_links_of_other_kinds_of_project(
        self, tmp_path: Path, files: dict[str, str | bytes], label: str
    ) -> None:
        result = self.site(tmp_path, files)
        assert (result.status, result.message) == OK
        assert result.details[-1] == f"Found documentation: {label} link"

    @pytest.mark.parametrize(
        "files",
        [
            {"pyproject.toml": project('dynamic = ["urls"]\n')},
            {
                "setup.py": "from setuptools import setup\n"
                "from links import LINKS\n"
                'setup(name="mypkg", project_urls=LINKS)\n'
            },
            {
                "setup.py": "from setuptools import setup\n"
                "from options import OPTIONS\n"
                "setup(**OPTIONS)\n"
            },
        ],
        ids=["dynamic urls", "computed project_urls", "computed keywords"],
    )
    def test_links_set_at_build_time_give_no_note(
        self, tmp_path: Path, files: dict[str, str | bytes]
    ) -> None:
        result = self.site(tmp_path, files)
        assert (result.status, result.message) == OK
        assert result.details[-1] == (
            "The project links are set when the package is built" + NO_LINK_SEARCH
        )

    @pytest.mark.parametrize(
        ("files", "reason"),
        [
            (
                {
                    "setup.py": "from setuptools import setup\n"
                    "about = {}\n"
                    'setup(name="mypkg", url=about["__url__"])\n'
                },
                "The project links are set when the package is built",
            ),
            (
                {
                    "setup.py": 'print "building"\n'
                    "from setuptools import setup\n"
                    'setup(project_urls={"Documentation": "https://x.rtfd.io"})\n'
                },
                "setup.py could not be read",
            ),
        ],
        ids=["computed url", "setup.py for Python 2"],
    )
    def test_links_that_cannot_be_read_give_no_note(
        self, tmp_path: Path, files: dict[str, str | bytes], reason: str
    ) -> None:
        result = self.site(tmp_path, files)
        assert (result.status, result.message) == OK
        assert result.details[-1] == reason + NO_LINK_SEARCH

    def test_a_readable_link_counts_beside_one_that_is_not(
        self, tmp_path: Path
    ) -> None:
        setup_py = (
            "from setuptools import setup\n"
            "from links import HOME\n"
            f'setup(url=HOME, project_urls={{"Documentation": "{SITE_LINK}"}})\n'
        )
        result = self.site(tmp_path, {"setup.py": setup_py})
        assert result.details[-1] == "Found documentation: Documentation link"

    def test_a_label_that_is_not_text_is_ignored(self, tmp_path: Path) -> None:
        """A number too long to print used to stop the check."""
        number = "0x" + "F" * 4000
        setup_py = (
            "from setuptools import setup\n"
            f'setup(name="mypkg", project_urls={{{number}: "{SITE_LINK}"}})\n'
        )
        result = self.site(tmp_path, {"setup.py": setup_py})
        assert found(result) == [MISSING_SITE]

    @pytest.mark.parametrize(
        "declared",
        [
            'readme = "docs/index.md"',
            'readme = {file = "docs/index.md", content-type = "text/markdown"}',
        ],
    )
    def test_the_readme_kept_in_docs_is_not_a_page(
        self, tmp_path: Path, declared: str
    ) -> None:
        files = {"pyproject.toml": project(declared), "docs/index.md": LONG}
        result = run(tmp_path, files, check_docs_site=True)
        assert found(result) == [MISSING_SITE]

    def test_a_linked_docs_directory_is_not_followed(self, tmp_path: Path) -> None:
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "index.md").write_text("# Elsewhere\n")
        root = tmp_path / "project"
        root.mkdir()
        try:
            os.symlink(outside, root / "docs", target_is_directory=True)
        except OSError:
            pytest.skip("cannot create a symbolic link here")
        result = self.site(root, {"pyproject.toml": project()})
        assert found(result) == [MISSING_SITE]

    def test_a_pyproject_that_cannot_be_read_gives_no_note(
        self, tmp_path: Path
    ) -> None:
        """The metadata check reports the file. Its links are unknown here."""
        result = self.site(tmp_path, {"pyproject.toml": "[project\nname ="})
        assert (result.status, result.message) == OK
        assert result.details[-1] == "pyproject.toml could not be read" + NO_LINK_SEARCH
        (tmp_path / "mkdocs.yml").write_text("site_name: x\n")
        again = DocsCheck().run(tmp_path, {"check_docs_site": True})
        assert again.details[-1] == "Found documentation: mkdocs.yml"

    def test_a_site_alone_is_something_to_check(self, tmp_path: Path) -> None:
        """With no README and none required, a found site is still a result."""
        result = run(
            tmp_path,
            {"mkdocs.yml": "site_name: x\n"},
            require_readme=False,
            check_docs_site=True,
        )
        assert (result.status, result.message) == OK


API = '''"""Api."""
from typing import overload


class Public:
    """Doc."""

    @property
    def x(self):
        """Doc."""
        return 1

    @x.setter
    def x(self, value):
        pass

    @overload
    def conv(self, v: int) -> int: ...
    @overload
    def conv(self, v: str) -> str: ...
    def conv(self, v):
        """Doc."""
        return v


class _Private:
    def public_method_of_private_class(self):
        pass


def documented():
    """Doc."""

    def closure():
        pass

    return closure
'''
UNDOCUMENTED = "class PublicClass:\n    pass\n\n\ndef public_func():\n    pass\n"
# Two of three public items are documented: both modules, not the function.
PARTLY = {
    "README.md": LONG,
    "pyproject.toml": project(),
    "src/mypkg/__init__.py": '"""Pkg."""\n',
    "src/mypkg/api.py": '"""Api."""\n\n\ndef f():\n    pass\n',
}
COVERAGE = "Docstring coverage: 2/3 public items (67%)"
# A project whose only module does not parse, and the detail line that says so.
UNPARSED = {"pyproject.toml": project(), "src/mypkg/__init__.py": "def f(:\n"}
COULD_NOT_PARSE = (
    "Could not parse 1 file(s), skipped: src/mypkg/__init__.py (syntax error"
)


class TestDocstrings:
    """The opt-in docstring coverage scan."""

    def test_only_public_items_are_counted(self, tmp_path: Path) -> None:
        files = {
            **PARTLY,
            "src/mypkg/api.py": API,
            "src/mypkg/_internal.py": "def helper():\n    pass\n",
            "src/mypkg/empty_sub.py": "",
        }
        result = run(tmp_path, files, check_docstrings=True)
        assert (result.status, result.message) == OK
        assert result.details[2:] == [
            "Docstring coverage: 6/6 public items (100%)",
            "All public items have docstrings",
        ]

    def test_missing_items_are_listed_with_their_path(self, tmp_path: Path) -> None:
        files = {**PARTLY, "src/mypkg/api.py": UNDOCUMENTED}
        result = run(tmp_path, files, check_docstrings=True)
        assert (result.status, result.message) == NOTE
        assert result.details[2:] == [
            "Docstring coverage: 1/4 public items (25%)",
            "Missing docstrings in 3 public item(s)",
            "  - src/mypkg/api.py: module",
            "  - src/mypkg/api.py: class PublicClass",
            "  - src/mypkg/api.py: function public_func",
        ]
        assert found(result) == [MISSING_DOCSTRINGS]
        assert result.findings[0].message == "Missing docstrings in 3 public item(s)"

    def test_the_most_severe_finding_sets_the_status(self, tmp_path: Path) -> None:
        files = {name: text for name, text in PARTLY.items() if name != "README.md"}
        result = run(tmp_path, files, check_docstrings=True)
        assert result.status == CheckStatus.WARNING
        assert result.message == "Documentation incomplete"
        assert found(result) == [MISSING_README, MISSING_DOCSTRINGS]

    def test_docstrings_alone_are_checked_when_no_readme_is_required(
        self, tmp_path: Path
    ) -> None:
        files = {name: text for name, text in PARTLY.items() if name != "README.md"}
        result = run(tmp_path, files, require_readme=False, min_docstring_coverage=50)
        assert (result.status, result.message) == OK
        assert result.details == [COVERAGE]

    @pytest.mark.parametrize(
        ("config", "status", "details"),
        [
            (
                {"min_docstring_coverage": 67},
                NOTE,
                [
                    COVERAGE,
                    "Docstring coverage 66.6% is below minimum 67%",
                    "Missing docstrings in 1 public item(s)",
                    "  - src/mypkg/api.py: function f",
                ],
            ),
            ({"min_docstring_coverage": 50}, OK, [COVERAGE]),
            ({"check_docstrings": True, "min_docstring_coverage": 50}, OK, [COVERAGE]),
            (
                {"check_docstrings": True},
                NOTE,
                [
                    COVERAGE,
                    "Missing docstrings in 1 public item(s)",
                    "  - src/mypkg/api.py: function f",
                ],
            ),
            (
                {"check_docstrings": True, "min_docstring_coverage": "80"},
                NOTE,
                [
                    COVERAGE,
                    "Missing docstrings in 1 public item(s)",
                    "  - src/mypkg/api.py: function f",
                ],
            ),
        ],
        ids=["below", "above", "above with the flag", "no minimum", "string minimum"],
    )
    def test_minimum_coverage_is_the_criterion(
        self,
        tmp_path: Path,
        config: dict[str, Any],
        status: tuple[Any, str],
        details: list[str],
    ) -> None:
        result = run(tmp_path, PARTLY, **config)
        assert (result.status, result.message) == status
        assert result.details[2:] == details
        # The finding is the first line that reports the shortfall.
        assert found(result) == ([MISSING_DOCSTRINGS] if status == NOTE else [])
        assert [finding.message for finding in result.findings] == details[1:2]

    @pytest.mark.parametrize(
        ("files", "detail"),
        [
            (
                {"src/mypkg/__init__.py": ""},
                "No public items found for the docstring check",
            ),
            (
                {"src/mypkg/__init__.py": None, "six.py": "def f():\n    pass\n"},
                "Missing docstrings in 2 public item(s)",
            ),
            (
                {
                    "pyproject.toml": project(name="acme-widgets"),
                    "src/mypkg/__init__.py": None,
                    "src/acme/widgets/__init__.py": "def f():\n    pass\n",
                },
                "Missing docstrings in 2 public item(s)",
            ),
        ],
        ids=["empty package", "single module", "namespace package"],
    )
    def test_coverage_is_never_zero_of_zero(
        self, tmp_path: Path, files: dict[str, str | None], detail: str
    ) -> None:
        merged = {**PARTLY, "src/mypkg/api.py": None, **files}
        kept = {name: text for name, text in merged.items() if text is not None}
        result = run(tmp_path, kept, check_docstrings=True)
        assert detail in result.details
        assert not any("0/0" in line for line in result.details)

    def test_minimum_is_met_at_equality(self, tmp_path: Path) -> None:
        files = {**PARTLY, "src/mypkg/api.py": "x = 1\n"}
        result = run(tmp_path, files, min_docstring_coverage=50)
        assert (result.status, result.message) == OK
        assert result.details[2:] == ["Docstring coverage: 1/2 public items (50%)"]

    @pytest.mark.parametrize(
        ("minimum", "status", "issue"),
        [
            (50, OK, None),
            (
                80,
                NOTE,
                "Docstring coverage 70% in the sampled files is below minimum 80%",
            ),
        ],
    )
    def test_sample_is_spread_over_the_package(
        self, tmp_path: Path, minimum: int, status: tuple[Any, str], issue: str | None
    ) -> None:
        """The 20 bare modules that sort first must not decide for the other 40."""
        files = {k: v for k, v in PARTLY.items() if k != "src/mypkg/api.py"}
        files.update({f"src/mypkg/a{i:02}.py": "x = 1\n" for i in range(20)})
        files.update({f"src/mypkg/z{i:02}.py": '"""Doc."""\n' for i in range(40)})
        result = run(tmp_path, files, min_docstring_coverage=minimum)
        assert (result.status, result.message) == status
        assert result.details[2:4] == [
            "Docstring scan sampled 20 of 61 files",
            "Docstring coverage: 14/20 public items (70%)",
        ]
        assert result.details[4:5] == ([issue] if issue else [])
        assert found(result) == ([MISSING_DOCSTRINGS] if issue else [])

    def test_sample_is_disclosed(self, tmp_path: Path) -> None:
        modules = {f"src/mypkg/m{i:02}.py": '"""Doc."""\n' for i in range(26)}
        modules["src/mypkg/api.py"] = '"""Api."""\n'
        result = run(tmp_path, {**PARTLY, **modules}, check_docstrings=True)
        assert (result.status, result.message) == OK
        assert result.details[2:] == [
            "Docstring scan sampled 20 of 28 files",
            "Docstring coverage: 20/20 public items (100%)",
        ]

    @pytest.mark.parametrize(
        "config",
        [{"check_docstrings": True}, {"min_docstring_coverage": 100}],
        ids=["no minimum", "a minimum"],
    )
    def test_unparsed_file_is_skipped_not_graded(
        self, tmp_path: Path, config: dict[str, Any]
    ) -> None:
        files = {**PARTLY, "src/mypkg/api.py": "def f(:\n"}
        result = run(tmp_path, files, **config)
        assert (result.status, result.message) == OK
        assert result.details[2].startswith(
            "Could not parse 1 file(s), skipped: src/mypkg/api.py (syntax error"
        )
        assert result.details[3:] == ["Docstring coverage: 1/1 public items (100%)"]

    @pytest.mark.parametrize(
        "config",
        [{"check_docstrings": True}, {"min_docstring_coverage": 80}],
        ids=["no minimum", "a minimum"],
    )
    @pytest.mark.parametrize(
        "files",
        [UNPARSED, {**UNPARSED, "src/mypkg/empty.py": ""}],
        ids=["no file parses", "the file that parses has nothing to count"],
    )
    def test_nothing_counted_and_a_file_left_out_is_parse_failed(
        self, tmp_path: Path, files: dict[str, str], config: dict[str, Any]
    ) -> None:
        """The docstring count was the only thing to do, and it could not be done."""
        result = run(tmp_path, files, require_readme=False, **config)
        assert result.status == CheckStatus.SKIPPED
        assert result.message == "Could not parse the package source"
        assert result.skip_reason == "parse-failed"
        assert result.details[0].startswith(COULD_NOT_PARSE)
        assert result.details[1:] == [
            "No README file found, and require_readme is false"
        ]
        assert result.findings == []

    def test_nothing_parsed_beside_a_readme_keeps_its_result(
        self, tmp_path: Path
    ) -> None:
        files = {**UNPARSED, "README.md": LONG}
        result = run(tmp_path, files, require_readme=False, check_docstrings=True)
        assert (result.status, result.message) == OK
        assert result.skip_reason is None
        assert result.details[2].startswith(COULD_NOT_PARSE)
        assert len(result.details) == 3

    @pytest.mark.parametrize(
        ("readme", "status", "expected"),
        [
            ({"README.md": LONG}, CheckStatus.NOTE, [NOT_COUNTED]),
            ({}, CheckStatus.WARNING, [MISSING_README, NOT_COUNTED]),
        ],
        ids=["beside a README", "no README"],
    )
    def test_minimum_with_nothing_counted_is_a_note(
        self,
        tmp_path: Path,
        readme: dict[str, str],
        status: CheckStatus,
        expected: list[tuple[str, CheckStatus]],
    ) -> None:
        """With a minimum set, a count that had nothing to count is reported."""
        result = run(tmp_path, {**UNPARSED, **readme}, min_docstring_coverage=80)
        assert result.status == status
        assert found(result) == expected
        assert any(line.startswith(COULD_NOT_PARSE) for line in result.details)
        assert result.details[-1] == (
            "No public item could be counted, so docstring coverage was not "
            "checked against minimum 80%"
        )
        assert result.findings[-1].message == result.details[-1]

    def test_minimum_with_nothing_to_count_is_ok(self, tmp_path: Path) -> None:
        """An empty package was read in full, so there is nothing to report."""
        files = {"README.md": LONG, "src/mypkg/__init__.py": ""}
        result = run(tmp_path, {**UNPARSED, **files}, min_docstring_coverage=80)
        assert (result.status, result.message) == OK
        assert found(result) == []
        assert result.details[-1] == "No public items found for the docstring check"


@pytest.mark.parametrize(
    ("code", "before", "after", "config", "said"),
    [
        # Fifty words are what the hint asks for, so no other rule fires.
        (
            "docs.missing-readme",
            {},
            {"README.md": "word " * 50},
            {},
            "Add a README.md file to the project root with at least 50 words",
        ),
        (
            "docs.readme-too-short",
            {"README.md": "word " * 49},
            {"README.md": "word " * 50},
            {},
            "Write at least 50 words in the README",
        ),
        (
            "docs.missing-installation",
            {"README.md": LONG},
            {"README.md": LONG + INSTALL},
            {"check_readme_sections": True},
            "Add an Installation section to the README",
        ),
        (
            "docs.missing-usage",
            {"README.md": LONG},
            {"README.md": LONG + USAGE},
            {"check_readme_sections": True},
            "Add a Usage section to the README",
        ),
        (
            "docs.missing-site",
            {"README.md": LONG, "pyproject.toml": project()},
            {"docs/index.md": "# Guide\n"},
            {"check_docs_site": True},
            "Add a page such as docs/index.md",
        ),
        (
            "docs.missing-docstrings",
            PARTLY,
            {"src/mypkg/api.py": '"""Api."""\n\n\ndef f():\n    """Do it."""\n'},
            {"check_docstrings": True},
            "Add a docstring to every public module, class, function and method",
        ),
        (
            "docs.docstrings-not-counted",
            {**UNPARSED, "README.md": LONG},
            {"src/mypkg/__init__.py": '"""Pkg."""\n'},
            {"min_docstring_coverage": 100},
            "Look in the details for the Python files that were left out and why",
        ),
    ],
)
def test_following_the_hint_clears_the_rule(
    tmp_path: Path,
    code: str,
    before: dict[str, str | bytes],
    after: dict[str, str | bytes],
    config: dict[str, Any],
    said: str,
) -> None:
    """Doing what a hint says makes its rule stop firing."""
    result = run(tmp_path, before, **config)
    fill_hints(result, DocsCheck.hints)
    hints = {finding.code: finding.hint or "" for finding in result.findings}
    assert hints[code].startswith(said)
    after_codes = dict(found(run(tmp_path, after, **config)))
    assert code not in after_codes
    if code == "docs.missing-readme":
        assert after_codes == {}


def test_read_permission_clears_unreadable(tmp_path: Path) -> None:
    """So does the hint for a README that cannot be read."""
    readme = tmp_path / "README.md"
    readme.write_text(LONG, encoding="utf-8")
    readme.chmod(0)
    if os.access(readme, os.R_OK):
        pytest.skip("a file with no permissions can still be read here")
    assert found(DocsCheck().run(tmp_path, {})) == [UNREADABLE_README]
    readme.chmod(0o400)  # chmod u+r README.md
    assert found(DocsCheck().run(tmp_path, {})) == []


SITE = {"README.md": LONG, "src/mypkg/__init__.py": ""}
LINK = 'Documentation = "https://mypkg.example.com/"\n'


@pytest.mark.parametrize(
    ("pyproject", "table"),
    [
        (project(), "[project.urls]"),
        ('[tool.poetry]\nname = "mypkg"\nversion = "1"\n', "[tool.poetry.urls]"),
    ],
)
def test_site_link_is_advised_in_the_table_the_project_has(
    tmp_path: Path, pyproject: str, table: str
) -> None:
    """The second way of the hint clears the rule and upsets no other check."""
    result = run(tmp_path, {**SITE, "pyproject.toml": pyproject}, check_docs_site=True)
    (finding,) = result.findings
    assert finding.hint == (
        "Add a page such as docs/index.md, or link a site other than the "
        f"repository page as Documentation in {table} of pyproject.toml."
    )
    before = MetadataCheck().run(tmp_path, {})
    clear_cache()
    linked = {"pyproject.toml": f"{pyproject}{table}\n{LINK}"}
    assert found(run(tmp_path, linked, check_docs_site=True)) == []
    after = MetadataCheck().run(tmp_path, {})
    assert before.status == after.status
    assert {f.code for f in after.findings} <= {f.code for f in before.findings}


def test_no_site_link_is_advised_without_a_metadata_table(tmp_path: Path) -> None:
    """A [project.urls] table there would start a [project] with no name."""
    files = {**SITE, "setup.cfg": "[metadata]\nname = mypkg\n"}
    (finding,) = run(tmp_path, files, check_docs_site=True).findings
    assert finding.hint == "Add a page such as docs/index.md."
