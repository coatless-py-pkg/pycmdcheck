"""Named constants used across pycmdcheck."""

# Subprocess execution
DEFAULT_TIMEOUT: int = 120
LONG_TIMEOUT: int = 300

# Output limits
MAX_DETAIL_LINES: int = 10
MAX_WORKERS: int = 4

# Documentation thresholds
MIN_README_WORDS: int = 50
MIN_LICENSE_LENGTH: int = 50
MAX_DOCSTRING_FILES: int = 20
MAX_TYPE_SCAN_FILES: int = 20

# URL validation
URL_TIMEOUT: int = 10
MAX_URL_CHECKS: int = 20

# Changelog validation
MIN_CHANGELOG_LENGTH: int = 50

# Python version EOL dates (year, month) — versions whose EOL has passed
# Source: https://devguide.python.org/versions/
PYTHON_EOL_VERSIONS: dict[str, tuple[int, int]] = {
    "3.8": (2024, 10),
    "3.9": (2025, 10),
}
