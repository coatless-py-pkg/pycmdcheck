"""Copy the Agent Skill that pycmdcheck carries into a skills directory.

``skills/pycmdcheck/SKILL.md`` tells an AI assistant how to run pycmdcheck.
The build puts that folder inside the package, so an installed copy carries
the skill that matches its version. ``pycmdcheck --install-skill`` hands it
out, with no clone of the repository.
"""

import os
from collections.abc import Iterator
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path

from pycmdcheck.fileio import os_error_text

# Where --install-skill puts the skill when it is given no directory.
DEFAULT_DIRECTORY = ".claude/skills"

# The name of the skill, which is also the name of its folder.
_NAME = "pycmdcheck"

_NO_SKILL = (
    "This copy of pycmdcheck carries no skill. Is pycmdcheck installed correctly?"
)


def _carried() -> Traversable | None:
    """Return the skill folder that this copy of pycmdcheck carries, or None.

    An installed copy holds it inside the package, where the build put it. In
    a source checkout and in an editable install the package is
    ``src/pycmdcheck``, which has no such folder. There the skill is in
    ``skills/``, beside ``src``. A package in a folder of any other name is
    an installed copy, and nothing above it is taken for the skill.
    """
    folders: list[Traversable] = [files("pycmdcheck") / ".agents" / "skills" / _NAME]
    src = Path(__file__).resolve().parent.parent
    if src.name == "src":
        folders.append(src.parent / "skills" / _NAME)
    for folder in folders:
        if (folder / "SKILL.md").is_file():
            return folder
    return None


def _files(
    folder: Traversable, parts: tuple[str, ...] = ()
) -> Iterator[tuple[tuple[str, ...], bytes]]:
    """Yield the path and the content of every file in *folder* and below it.

    A name that starts with a dot is left out, such as the ``.DS_Store``
    that macOS adds to a folder.
    """
    for entry in folder.iterdir():
        if entry.name.startswith("."):
            continue
        if entry.is_dir():
            yield from _files(entry, (*parts, entry.name))
        elif entry.is_file():
            yield (*parts, entry.name), entry.read_bytes()


def install(directory: str) -> str:
    """Copy the carried skill into *directory* and return the line to print.

    *directory* is a skills directory, such as ``.claude/skills``, and the
    skill goes into its ``pycmdcheck`` folder. Missing directories are
    created. A file that is already the same is not written again.

    Raises:
        ValueError: With a line for the person, if *directory* is not named
            skills or starts with a home directory that cannot be found, if
            this copy of pycmdcheck carries no skill or one that cannot be
            read, if a symbolic link stands where the skill goes, or if a
            write fails.
    """
    expanded = os.path.expanduser(directory)
    skills = Path(expanded)
    if skills.name != "skills":
        # Without this rule "--install-skill mypackage" would take the
        # package that was meant to be checked and write a folder into it.
        raise ValueError(
            f'--install-skill read "{directory}" as its directory. The '
            f"directory must be named skills, as in {DEFAULT_DIRECTORY}."
        )
    if expanded.startswith("~"):
        # The name of a user that does not exist is left as it is, and the
        # skill would go into a new folder with that name.
        raise ValueError(
            "--install-skill cannot find the home directory that "
            f'"{directory}" starts with.'
        )
    try:
        source = _carried()
        carried = [] if source is None else list(_files(source))
    except OSError as exc:
        raise ValueError(
            f"Cannot read the skill that pycmdcheck carries: {os_error_text(exc)}"
        ) from exc
    if not carried:
        raise ValueError(_NO_SKILL)
    target = skills / _NAME
    shown = target / "SKILL.md"
    wanted = {target.joinpath(*parts): data for parts, data in carried}

    # Another tool links skills into such directories. A write through its
    # link would change a file inside an installed package. So the skill
    # folder, each file and each folder between the two must be no link.
    checked = {target} | {
        part
        for path in wanted
        for part in (path, *path.parents)
        if target in part.parents
    }
    try:
        # The test for a link is in here as well. Before Python 3.14 it
        # raises for a directory that cannot be entered.
        link = next((path for path in sorted(checked) if path.is_symlink()), None)
        if link is not None:
            raise ValueError(
                f"Nothing was written because {link} is a symbolic link, so "
                "remove the link or pick another directory."
            )
        existed = shown.exists()
        changed = {
            path: data
            for path, data in wanted.items()
            if not (path.is_file() and path.read_bytes() == data)
        }
        # Each file is written in place, so a hard link is written through.
        # The rest is right for a skill of one file. With more files a copy
        # would get no executable bit, a file that the skill no longer has
        # would stay, and a write that fails would keep the files written
        # before it and still name SKILL.md.
        for path, data in changed.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
    except OSError as exc:
        raise ValueError(
            f"Cannot write the skill to {shown}: {os_error_text(exc)}"
        ) from exc
    if not changed:
        return f"The skill in {shown} is already up to date."
    return f"{'Updated' if existed else 'Installed'} the skill in {shown}."
