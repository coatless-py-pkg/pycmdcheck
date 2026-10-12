"""Entry point for python -m pycmdcheck."""

import os
import sys

if __name__ == "__main__" and sys.path and sys.path[0] in ("", os.getcwd()):
    # "python -m" puts the current directory first on sys.path. A repository
    # checked from its own root could then ship entry points that get loaded
    # as checks. Dropping that entry prevents that. It comes too late for the
    # modules pycmdcheck has already imported, so an untrusted repository
    # should be checked with the "pycmdcheck" command or "python -P -m
    # pycmdcheck", with the repository passed as PATH.
    del sys.path[0]

from pycmdcheck.cli import main  # noqa: E402

if __name__ == "__main__":
    main()
