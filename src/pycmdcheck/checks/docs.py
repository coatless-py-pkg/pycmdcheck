"""Documentation check.

This module provides the DocsCheck class which verifies that a package
has proper documentation including a README file and optionally checks
for docstrings in the code and for documentation beyond the README.
"""

import ast
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from pycmdcheck.ast_cache import parse_file, unparsed_files_detail
from pycmdcheck.checks.base import BaseCheck
from pycmdcheck.checks.changelog import urls_table
from pycmdcheck.constants import MAX_DOCSTRING_FILES, MIN_README_WORDS
from pycmdcheck.fileio import (
    display_path,
    find_declared_file,
    find_declared_files,
    find_project_file,
    find_subdir,
    is_regular_file,
    os_error_text,
    plain_text,
    read_text_capped,
    resolve_declared_file,
)
from pycmdcheck.package_layout import PackageLayout
from pycmdcheck.pyproject_reader import (
    SETUP_PY_DYNAMIC,
    get_effective_project_table,
    get_number,
    get_setup_cfg_value,
    get_setup_py_keywords,
    get_str_dict,
    is_dynamic,
    pyproject_status,
)
from pycmdcheck.results import CheckResult, CheckStatus, Finding, most_severe

# Topics looked for in the lower-cased README when check_readme_sections is
# on. A code block that shows Python in use counts as usage. A shell prompt
# does not, or "$ pip install pkg" would count as usage too. Each topic has a
# rule, "docs.missing-" and the topic in lower case.
README_TOPICS = {
    "Installation": r"install|\bsetup\b|\b(?:uv|pixi) add\b",
    "Usage": (
        r"usage|quick[\s-]?start|getting[\s-]?started|example|tutorial|how to use"
        r"|\bdemo\b|walkthrough|^[ \t]*(?:>>>|import \w|from [\w.]+ import )"
    ),
}
# First character of the CJK blocks. A README in a language written without
# spaces is counted by its characters from here up.
CJK_START = "\u2e80"

# Directories a project keeps its documentation pages in.
DOCS_DIRS = ("docs", "doc", "documentation")
# Files that set up a documentation site, as patterns relative to the project
# root: Read the Docs, MkDocs, Zensical, Quarto, Jupyter Book and Sphinx. A
# pattern ending in ".yml" accepts ".yaml" as well.
SITE_PATTERNS = [
    ".readthedocs.yml",
    "readthedocs.yml",
    "mkdocs.yml",
    "zensical.toml",
    "_quarto.yml",
    "myst.yml",
    "_toc.yml",
    *(
        f"{directory}/{name}"
        for directory in DOCS_DIRS
        for name in (
            "conf.py",
            "source/conf.py",
            "mkdocs.yml",
            "_quarto.yml",
            "myst.yml",
            "_toc.yml",
        )
    ),
]
# A file in or below one of DOCS_DIRS counts as a page by its suffix, unless
# it, or a directory it lies in, has one of these names. They are the files
# GitHub also reads from docs/, which are not about using the package.
PAGE_SUFFIXES = (".md", ".rst", ".qmd", ".ipynb", ".adoc")
_NOT_PAGES = frozenset(
    {
        "readme",
        "contributing",
        "code_of_conduct",
        "security",
        "support",
        "changelog",
        "governance",
        "issue_template",
        "pull_request_template",
    }
)
# A link label that names documentation ("Documentation", "Docs", "API docs"),
# and the hosts that serve little else.
_DOCS_LABEL_RE = re.compile(r"doc(?:s|umentation)?\b")
_DOCS_HOSTS = (
    ".readthedocs.io",
    ".readthedocs.org",
    ".rtfd.io",
    ".github.io",
    ".gitlab.io",
)
# Hosts where the front page of a repository shows its README.
_CODE_HOSTS = frozenset({"github.com", "gitlab.com", "bitbucket.org", "codeberg.org"})
# The hint for a project with no documentation beyond its README. The link
# is only named for a project that keeps its metadata in pyproject.toml.
_ADD_PAGE = "Add a page such as docs/index.md{}."
_LINK = (
    ", or link a site other than the repository page as Documentation in {} of "
    "pyproject.toml"
)
# The hint for a README that could not be read, with the file to name.
_READ = "Give yourself read permission on the README, for example with chmod u+r {}."


def _is_docs_link(label: str, url: str) -> bool:
    """Return whether a project link points at documentation.

    A link labelled as documentation does not count when it only leads to
    the front page or the README of a repository, since that is the README
    again.
    """
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").casefold()
    except ValueError:
        return False
    if host.endswith(_DOCS_HOSTS):
        return True
    if not _DOCS_LABEL_RE.search(label.casefold()):
        return False
    if host.removeprefix("www.") not in _CODE_HOSTS:
        return True
    path = parts.path.casefold()
    return len([part for part in path.split("/") if part]) > 2 and "readme" not in path


class DocsCheck(BaseCheck):
    """Check for documentation presence and quality.

    Verifies that the package has a README file and optionally checks
    that public modules, classes, and functions have docstrings, and that
    the project has documentation beyond the README.

    Attributes:
        name: The check identifier ("docs").
        description: Human-readable description of this check.
        rules: Every code the check can emit, with what the rule means.
        hints: The same codes, with what to do about each finding.
        README_FILENAMES: List of recognized README file names, most
            preferred first. Any other ``README*`` name is found after them.

    Configuration Options:
        require_readme (bool): Whether README is required. Defaults to True.
        check_docstrings (bool): Whether to check for docstrings in code.
            Defaults to False.
        check_readme_sections (bool): Whether to look for installation and
            usage text in the README. Defaults to False.
        check_docs_site (bool): Whether to look for documentation beyond the
            README: the setup of a site builder, pages in a docs directory,
            or a documentation link in the project metadata. Defaults to
            False.
        min_docstring_coverage (float): Minimum docstring coverage percentage
            (0.0-100.0). A value above 0 turns the docstring scan on and
            becomes its pass mark. Defaults to 0.0 (disabled).

    Examples:
        Run the docs check with defaults:

        >>> from pathlib import Path
        >>> check = DocsCheck()
        >>> result = check.run(Path("."), {"enabled": True})
        >>> result.name
        'docs'

        Require half of the public items to have a docstring:

        >>> config = {"enabled": True, "min_docstring_coverage": 50}
        >>> result = check.run(Path("."), config)
    """

    name = "docs"
    description = "Check documentation (README, docstrings)"
    rules = {
        "docs.missing-readme": "No README file was found",
        "docs.unreadable-readme": "The README could not be read",
        "docs.readme-too-short": f"The README has fewer than {MIN_README_WORDS} words",
        "docs.missing-installation": "The README does not mention installation",
        "docs.missing-usage": "The README does not mention usage",
        "docs.missing-site": "No documentation beyond the README was found",
        "docs.missing-docstrings": (
            "Public items have no docstring, or fewer have one than the minimum"
        ),
        "docs.docstrings-not-counted": (
            "A minimum docstring coverage is set and no public item was counted "
            "because a file could not be parsed"
        ),
    }
    hints = {
        "docs.missing-readme": (
            f"Add a README.md file to the project root with at least "
            f"{MIN_README_WORDS} words on what the package does and how to use it."
        ),
        "docs.unreadable-readme": _READ.format("README.md"),
        "docs.readme-too-short": (
            f"Write at least {MIN_README_WORDS} words in the README about what the "
            "package does and how to use it."
        ),
        "docs.missing-installation": (
            "Add an Installation section to the README that shows the command to "
            "install the package."
        ),
        "docs.missing-usage": (
            "Add a Usage section to the README with a short example of the package "
            "in use."
        ),
        "docs.missing-site": _ADD_PAGE.format(_LINK.format("[project.urls]")),
        "docs.missing-docstrings": (
            "Add a docstring to every public module, class, function and method "
            "that has none."
        ),
        "docs.docstrings-not-counted": (
            "Look in the details for the Python files that were left out and why, "
            "and put that right so that their docstrings can be counted."
        ),
    }

    README_FILENAMES = [
        "README.md",
        "README.rst",
        "README.txt",
        "README",
    ]

    def run(self, package_path: Path, config: dict[str, Any]) -> CheckResult:
        """Check documentation presence and quality.

        Looks for a README file and optionally scans code for missing
        docstrings on public items and looks for documentation beyond the
        README.

        Args:
            package_path: Path to the package directory to check.
            config: Configuration dictionary with options:

                - require_readme (bool): Require README (default: True)
                - check_docstrings (bool): Check code docstrings (default: False)
                - check_readme_sections (bool): Look for installation and
                  usage text in the README (default: False)
                - check_docs_site (bool): Look for documentation beyond the
                  README (default: False)
                - min_docstring_coverage (float): Minimum coverage %
                  (default: 0.0, disabled)

        Returns:
            A CheckResult with:

            - OK status if all documentation requirements met
            - NOTE status if minor improvements suggested
            - WARNING status if README missing (when required) or cannot be read
            - SKIPPED status ("not-applicable") if there is no README, none
              is required, no docstring was counted and no documentation
              beyond the README was looked for
            - SKIPPED status ("parse-failed") in that same case, if the
              docstring count was asked for and a sampled file could not
              be parsed
        """
        details: list[str] = []
        # Each shortcoming is kept as a detail line and as a finding.
        issues: list[str] = []
        findings: list[Finding] = []

        # Check for README
        require_readme = config.get("require_readme", True)
        sections = bool(config.get("check_readme_sections", False))
        readme_found, readme = self._check_readme(
            package_path, details, issues, findings, require_readme, sections
        )

        # Check README topics (opt-in)
        if sections and readme:
            lowered = readme.lower()
            for topic, pattern in README_TOPICS.items():
                if not re.search(pattern, lowered, re.MULTILINE):
                    issue = f"README does not appear to mention: {topic}"
                    issues.append(issue)
                    findings.append(
                        Finding(
                            f"docs.missing-{topic.lower()}", issue, CheckStatus.NOTE
                        )
                    )

        # Check for documentation beyond the README (opt-in)
        site_checked = bool(config.get("check_docs_site", False))
        if site_checked:
            self._check_site(package_path, details, issues, findings)

        # Check for docstrings (optional). A minimum above 0 implies the scan.
        min_coverage = get_number(
            config, "min_docstring_coverage", 0.0, minimum=0, maximum=100
        )
        counted: bool | None = False
        if config.get("check_docstrings", False) or min_coverage > 0:
            counted = self._check_docstrings(
                package_path, details, issues, findings, min_coverage
            )

        # Determine status
        if not (readme_found or counted or site_checked or findings):
            # No README, none required, no docstring counted and no site
            # looked for. A docstring count that left a file out and counted
            # nothing did not look, which is not the same as nothing to look
            # at.
            unread = counted is None
            return CheckResult.skip(
                self.name,
                "Could not parse the package source"
                if unread
                else "No documentation to check",
                "parse-failed" if unread else "not-applicable",
                [*details, "No README file found, and require_readme is false"],
            )
        if counted is None and min_coverage > 0:
            # A minimum was asked for and could not be checked.
            issue = (
                "No public item could be counted, so docstring coverage was not "
                f"checked against minimum {min_coverage:g}%"
            )
            issues.append(issue)
            findings.append(
                Finding("docs.docstrings-not-counted", issue, CheckStatus.NOTE)
            )
        if not findings:
            return CheckResult(
                name=self.name,
                status=CheckStatus.OK,
                message="Documentation present",
                details=details,
            )
        status = most_severe(findings)
        return CheckResult(
            name=self.name,
            status=status,
            message=(
                "Documentation incomplete"
                if status is CheckStatus.WARNING
                else "Some documentation improvements suggested"
            ),
            details=details + issues,
            findings=findings,
        )

    def _check_readme(
        self,
        package_path: Path,
        details: list[str],
        issues: list[str],
        findings: list[Finding],
        require_readme: bool,
        sections: bool = False,
    ) -> tuple[bool, str]:
        """Find the README and check its length.

        The file named by ``readme`` in pyproject.toml comes first, then the
        ``README_FILENAMES`` and then any other ``README*``, each looked for
        in the project root, ``.github/`` and ``docs/`` without regard to case.
        A README that is found and cannot be read is reported whether or not
        one is required.

        Returns:
            Whether a README exists, and its text ("" when there is none to read).
        """
        project = get_effective_project_table(package_path)
        declared = project.get("readme")
        text: str | None = None
        if isinstance(declared, dict):
            inline = declared.get("text")
            text = inline if isinstance(inline, str) else None
            declared = declared.get("file")
        readme = resolve_declared_file(package_path, declared)
        if isinstance(declared, str) and readme is None:
            details.append(
                f'pyproject.toml declares readme = "{plain_text(declared)}",'
                " which was not found inside the project"
            )
        search = ("", ".github", "docs")
        readme = (
            readme
            or find_project_file(package_path, self.README_FILENAMES, ("",), search)
            or find_project_file(package_path, ["README"], None, search)
        )

        if readme is not None:
            shown = display_path(readme, package_path)
            details.append(f"Found README: {shown}")
            try:
                text = read_text_capped(readme)
            except OSError as exc:
                issue = f"Could not read {shown}: {os_error_text(exc)}"
                # The hint names the file that was found.
                hint = _READ.format(shown)
                findings.append(
                    Finding("docs.unreadable-readme", issue, CheckStatus.WARNING, hint)
                )
                unchecked = "The length of the README was not checked"
                if sections:
                    unchecked += ", and installation and usage text was not looked for"
                issues.extend([issue, unchecked])
                return True, ""
        elif text is not None:
            details.append("README text is declared inline in pyproject.toml")
        elif is_dynamic(project, "readme"):
            details.append("README is declared as dynamic in pyproject.toml")
            return True, ""
        else:
            if require_readme:
                issue = "No README file found in the project root, .github/ or docs/"
                issues.append(issue)
                findings.append(
                    Finding("docs.missing-readme", issue, CheckStatus.WARNING)
                )
            return False, ""

        # Check README is not too short
        word_count = max(len(text.split()), sum(ch >= CJK_START for ch in text) // 2)
        if word_count < MIN_README_WORDS:
            issue = f"README is very short (< {MIN_README_WORDS} words)"
            issues.append(issue)
            findings.append(Finding("docs.readme-too-short", issue, CheckStatus.NOTE))
        else:
            details.append(f"README has {word_count} words")
        return True, text

    def _check_site(
        self,
        package_path: Path,
        details: list[str],
        issues: list[str],
        findings: list[Finding],
    ) -> None:
        """Look for documentation beyond the README.

        One of three things is enough: the setup of a site builder, a page
        in or below one of ``DOCS_DIRS``, or a documentation link in the
        project metadata. Nothing is reported when no such link is found
        and some of the links could not be read.
        """
        patterns = list(SITE_PATTERNS)
        patterns += [p[:-4] + ".yaml" for p in SITE_PATTERNS if p.endswith(".yml")]
        found = find_declared_file(package_path, patterns) or self._page(package_path)
        if found is not None:
            shown = display_path(found, package_path)
            details.append(f"Found documentation: {shown}")
            return

        links, unread = self._links(package_path)
        for label, url in links.items():
            if _is_docs_link(label, url):
                details.append(f"Found documentation: {plain_text(label)} link")
                return
        if unread is not None:
            details.append(f"{unread}, so a documentation link could not be looked for")
            return

        issue = "No documentation beyond the README found"
        table = urls_table(package_path)
        hint = _ADD_PAGE.format(_LINK.format(table) if table else "")
        findings.append(Finding("docs.missing-site", issue, CheckStatus.NOTE, hint))
        issues.append(issue)
        issues.append(
            "Looked for the setup of Sphinx, MkDocs, Quarto, Jupyter Book or "
            "Read the Docs, a documentation link in the project metadata, and "
            "pages in docs/, doc/ or documentation/"
        )

    @staticmethod
    def _page(package_path: Path) -> Path | None:
        """Return a documentation page in or below one of ``DOCS_DIRS``.

        The README does not count when the project keeps it there, and a
        symbolic link to a directory is not followed.
        """
        readme = get_effective_project_table(package_path).get("readme")
        if isinstance(readme, dict):
            readme = readme.get("file")
        declared_readme = resolve_declared_file(package_path, readme)
        for name in DOCS_DIRS:
            directory = find_subdir(package_path, name)
            if directory is None:
                continue
            pages = find_declared_files(
                directory, [f"**/*{suffix}" for suffix in PAGE_SUFFIXES], limit=200
            )
            for page in pages:
                names = page.relative_to(directory).parts
                if page != declared_readme and not any(
                    part.casefold().split(".")[0] in _NOT_PAGES for part in names
                ):
                    return page
        return None

    @staticmethod
    def _links(package_path: Path) -> tuple[dict[str, str], str | None]:
        """Return the project's links by label, and why some may be missing.

        Reads ``[project.urls]`` (or the legacy Poetry keys), then the
        ``project_urls`` and ``url`` of setup.py and setup.cfg. The second
        value is ``None`` when every link could be read. Otherwise it is the
        start of a sentence that says why not.
        """
        unread = None
        if pyproject_status(package_path) == "unparseable":
            unread = "pyproject.toml could not be read"
        project = get_effective_project_table(package_path)
        links = get_str_dict(project, "urls")
        computed = is_dynamic(project, "urls")

        setup_py = package_path / "setup.py"
        if is_regular_file(setup_py) and parse_file(setup_py) is None:
            unread = unread or "setup.py could not be read"
        keywords = get_setup_py_keywords(package_path)
        declared = keywords.get("project_urls", keywords.get("**"))
        homepage = keywords.get("url", keywords.get("**"))
        computed = computed or SETUP_PY_DYNAMIC in (declared, homepage)
        if isinstance(declared, dict):
            for label, url in declared.items():
                if isinstance(label, str) and isinstance(url, str):
                    links.setdefault(label, url)
        listed = get_setup_cfg_value(package_path, "metadata", "project_urls") or ""
        for line in listed.splitlines():
            label, _, url = line.partition("=")
            if url.strip():
                links.setdefault(label.strip(), url.strip())
        for value in (homepage, get_setup_cfg_value(package_path, "metadata", "url")):
            if isinstance(value, str):
                links.setdefault("Homepage", value)
        if computed:
            unread = unread or "The project links are set when the package is built"
        return links, unread

    def _check_docstrings(
        self,
        package_path: Path,
        details: list[str],
        issues: list[str],
        findings: list[Finding],
        min_coverage: float = 0.0,
    ) -> bool | None:
        """Measure docstring coverage of public items in a sample of modules.

        With *min_coverage* above 0 that percentage is the pass mark.
        Otherwise any public item without a docstring is reported.

        Returns:
            Whether any public item was found to count, or ``None`` when
            none was found and a sampled file could not be parsed.
        """
        # Runtime modules only, without private ones (_name.py, _name/).
        files = [
            f
            for f in PackageLayout(package_path).source_files()
            if not any(
                part.startswith("_") and part != "__init__.py"
                for part in f.relative_to(package_path).parts
            )
        ]
        # An evenly spaced sample, so that no one sub-package decides.
        count = min(len(files), MAX_DOCSTRING_FILES)
        sample = [files[i * len(files) // count] for i in range(count)]

        missing: list[str] = []
        total = 0
        skipped: list[Path] = []
        for py_file in sample:
            tree = parse_file(py_file)
            if tree is None:
                skipped.append(py_file)
                continue
            shown = display_path(py_file, package_path)
            for label, documented in self._public_items(tree):
                total += 1
                if not documented:
                    missing.append(f"{shown}: {label}")

        if len(sample) < len(files):
            details.append(
                f"Docstring scan sampled {len(sample)} of {len(files)} files"
            )
        unparsed = unparsed_files_detail(skipped, package_path)
        if unparsed:
            details.append(unparsed)
        if not total:
            if skipped:
                # A file was left out, so the code may hold items after all.
                return None
            details.append("No public items found for the docstring check")
            return False

        pct = (total - len(missing)) / total * 100
        details.append(
            f"Docstring coverage: {total - len(missing)}/{total}"
            f" public items ({pct:.0f}%)"
        )
        summary = f"Missing docstrings in {len(missing)} public item(s)"
        # The finding says how far coverage is below the minimum, if one is set.
        found = summary
        if min_coverage > 0:
            if pct >= min_coverage:
                return True
            sampled = " in the sampled files" if len(sample) < len(files) else ""
            # Cut, not rounded, so the figure shown is never the minimum itself.
            found = (
                f"Docstring coverage {int(pct * 10) / 10:g}%{sampled}"
                f" is below minimum {min_coverage:g}%"
            )
            issues.append(found)
        elif not missing:
            # Not claimed for a scan that left files out.
            if len(sample) == len(files) and not skipped:
                details.append("All public items have docstrings")
            return True

        issues.append(summary)
        findings.append(Finding("docs.missing-docstrings", found, CheckStatus.NOTE))
        for missing_item in missing[:5]:
            issues.append(f"  - {missing_item}")
        if len(missing) > 5:
            issues.append(f"  ... and {len(missing) - 5} more")
        return True

    @staticmethod
    def _public_items(tree: ast.Module) -> Iterator[tuple[str, bool]]:
        """Yield ``(label, has docstring)`` for a module and its public API.

        Only the module body and the bodies of public classes are walked, so
        closures and members of private classes are left out. ``@overload``
        stubs, property setters and deleters, and empty modules do not count.
        """
        if tree.body:
            yield "module", bool(ast.get_docstring(tree))
        pending: list[ast.Module | ast.ClassDef] = [tree]
        while pending:
            for node in pending.pop().body:
                if isinstance(node, ast.ClassDef):
                    kind = "class"
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    kind = "function"
                else:
                    continue
                if node.name.startswith("_"):
                    continue
                decorators = {
                    getattr(d, "attr", getattr(d, "id", ""))
                    for d in node.decorator_list
                }
                if decorators & {"overload", "setter", "deleter"}:
                    continue
                if isinstance(node, ast.ClassDef):
                    pending.append(node)
                yield f"{kind} {node.name}", bool(ast.get_docstring(node))
