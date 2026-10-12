"""Named constants used across pycmdcheck."""

import datetime

from pycmdcheck._stdlib_names import STDLIB_MODULE_NAMES as STDLIB_MODULE_NAMES

# Subprocess execution
DEFAULT_TIMEOUT: int = 120
LONG_TIMEOUT: int = 300

# Output limits
MAX_DETAIL_LINES: int = 10
MAX_WORKERS: int = 4

# File reading limits (bytes). Repository files are untrusted, so every read
# goes through pycmdcheck.fileio with one of these caps.
# Prose files (README, LICENSE, changelog, citation, setup.cfg) are cut off at
# the cap and the part that was read is still evaluated.
MAX_PROSE_BYTES: int = 1024 * 1024
# Python sources above the cap are skipped, since half a module does not parse.
# A syntax tree needs 40 to 700 times the memory of its source (measured on
# CPython 3.12), so a file at the cap made of nothing but "x=1" can take
# about 1.3 GB while it is parsed. The cap does not bound the time a parse
# takes. Trees stay cached until the run ends (see pycmdcheck.ast_cache).
MAX_SOURCE_BYTES: int = 2 * 1024 * 1024
# A pyproject.toml above the cap is reported as unparseable.
MAX_PYPROJECT_BYTES: int = 1024 * 1024
# Tables and arrays nested deeper than this make pyproject.toml unparseable.
MAX_TOML_DEPTH: int = 64

# Documentation thresholds
MIN_README_WORDS: int = 50
MIN_LICENSE_LENGTH: int = 50
MAX_DOCSTRING_FILES: int = 20
MAX_TYPE_SCAN_FILES: int = 20

# py.typed: share of public functions that must carry an annotation before the
# check suggests adding the marker.
PY_TYPED_MIN_ANNOTATED_RATIO: float = 0.5

# URL validation
URL_TIMEOUT: int = 10
# The per-request timeout can be configured, but only inside these bounds,
# because the configuration comes from the package being checked.
URL_TIMEOUT_MIN: int = 1
URL_TIMEOUT_MAX: int = 30
# Wall-clock budget, in seconds, for all requests of one urls check.
URL_OVERALL_DEADLINE: int = 60
MAX_URL_CHECKS: int = 20

# Changelog validation
MIN_CHANGELOG_LENGTH: int = 50

# Python end-of-life dates, keyed by "major.minor".
# Source: https://devguide.python.org/versions/ (read on 2026-10-09).
# Versions that have reached end of life carry the published date. Later
# versions carry the last day of their scheduled month, so a version is never
# reported before its final day. Every version older than the first entry is
# end-of-life as well.
PYTHON_EOL_DATES: dict[str, datetime.date] = {
    "3.6": datetime.date(2021, 12, 23),
    "3.7": datetime.date(2023, 6, 27),
    "3.8": datetime.date(2024, 10, 7),
    "3.9": datetime.date(2025, 10, 31),
    "3.10": datetime.date(2026, 10, 1),
    "3.11": datetime.date(2027, 10, 31),  # scheduled
    "3.12": datetime.date(2028, 10, 31),  # scheduled
    "3.13": datetime.date(2029, 10, 31),  # scheduled
    "3.14": datetime.date(2030, 10, 31),  # scheduled
    "3.15": datetime.date(2031, 10, 31),  # scheduled
}

# Days after its end-of-life date during which a version is not yet reported.
# Most packages still allow a version in the months after it retires, so the
# check waits about half a year before it starts to mention it.
EOL_GRACE_DAYS: int = 180

# Deprecated. Month-granular table kept so existing imports keep working. It is
# frozen at its 0.1.1 contents. Use PYTHON_EOL_DATES instead.
PYTHON_EOL_VERSIONS: dict[str, tuple[int, int]] = {
    "3.8": (2024, 10),
    "3.9": (2025, 10),
}
