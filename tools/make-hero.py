#!/usr/bin/env python3
"""Generate the README hero graphic in matched light and dark variants.

One specification, two files out, so the pair cannot drift. Run from anywhere:

    python3 tools/make-hero.py

Three steps. The file tree in step one and the path in step two show an invented
package, riverplot, as an example. The report in step three is the verbatim
output of

    pycmdcheck . --profile triage

run against this repository, so the graphic cannot promise something the tool
does not actually print. The caption under the graphic says where the report
comes from. Re-capture it and update RESULTS, SUMMARY, CLOSING, CLOSING_COLOR
and DESC if the output changes. The report card sizes itself to the rows, and
the script stops if a message or the summary line would run past the card.
"""

from __future__ import annotations

import pathlib

ASSETS = pathlib.Path(__file__).resolve().parent.parent / "assets"

SANS = "system-ui, -apple-system, sans-serif"
MONO = "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"

LIGHT = {
    "page": None,
    "panel": "#f1f5f9",
    "panelStroke": "#e2e8f0",
    "card": "#ffffff",
    "text": "#334155",
    "body": "#64748b",
    "faint": "#94a3b8",
    "rule": "#cbd5e1",
    "step": "#1e40af",
    "stepText": "#ffffff",
    "dir": "#1e40af",
    "accent": "#0f766e",
    "ok": "#166534",
    "note": "#1e40af",
    "skipped": "#64748b",
    "caution": "#92400e",
}

DARK = {
    "page": "#0f172a",
    "panel": "#16213e",
    "panelStroke": "#243049",
    "card": "#0b1730",
    "text": "#e2e8f0",
    "body": "#94a3b8",
    "faint": "#64748b",
    "rule": "#475569",
    "step": "#60a5fa",
    "stepText": "#0f172a",
    "dir": "#60a5fa",
    "accent": "#5eead4",
    "ok": "#4ade80",
    "note": "#93c5fd",
    "skipped": "#94a3b8",
    "caution": "#fbbf24",
}

# The marker the terminal prints for each status, and the palette entry that
# colours it. A skipped check is grey, because it is neither good nor bad.
GLYPH_COLOR = {"✓": "ok", "ℹ": "note", "○": "skipped"}

# Verbatim from `pycmdcheck . --profile triage`.
RESULTS = [
    ("✓", "changelog", "Changelog present"),
    ("✓", "ci", "CI configured (GitHub Actions)"),
    ("✓", "citation", "Citation file present"),
    ("✓", "community", "Community health files present"),
    ("✓", "docs", "Documentation present"),
    ("✓", "license", "License file present"),
    ("✓", "metadata", "Package metadata is valid"),
    ("✓", "py_typed", "py.typed marker present"),
    ("✓", "python_versions", "Python version requirement is current"),
    ("✓", "structure", "Valid src layout structure"),
    ("✓", "test_suite", "Test suite present"),
    ("○", "version", "Version set dynamically (e.g. importlib.metadata)"),
]
# The two lines the command prints under the table.
SUMMARY = "11 ok | 1 skipped"
CLOSING = "All checks passed!"
# The terminal prints "All checks passed!" in green ("ok") and a closing line
# that mentions warnings or skipped checks in yellow ("caution").
CLOSING_COLOR = "ok"

TREE = [
    ("riverplot/", 0),
    ("pyproject.toml", 1),
    ("README.md", 1),
    ("LICENSE", 1),
    ("CHANGELOG.md", 1),
    ("src/riverplot/", 1),
    ("tests/", 1),
]

TITLE = "Check a Python package without installing it"
DESC = (
    "Three steps. One, a package you did not write, fetched but never installed. "
    "Two, a single pycmdcheck command using the triage profile. Three, a health "
    "report of twelve checks covering the changelog, continuous integration, "
    "citation file, community files, documentation, license, metadata, py.typed "
    "marker, supported Python versions, source layout, test suite and version, "
    "with a summary of eleven ok and one skipped. The report shown is the output "
    "for pycmdcheck's own repository, where the version check is skipped because "
    "the package looks its version up when it runs, which leaves nothing to "
    "compare. Nothing in the "
    "package is built, imported or executed at any point."
)

STEPS = [
    (20, 170, "A SUBMITTED PACKAGE"),
    (222, 186, "ONE COMMAND"),
    (440, 460, "A HEALTH REPORT"),
]
# Clear space between each end of an arrow and the panel border beside it.
ARROW_GAP = 5

# The report card. The message column and the card height follow from RESULTS,
# so a long check name cannot run into its message and the summary line keeps
# clear space above the bottom border.
ROW_SIZE = 9.5
ROW_PITCH = 17
# The advance of one character. SF Mono is the widest font in MONO, at 0.62 em.
CHAR_WIDTH = 0.62 * ROW_SIZE
NAME_X = 40
COLUMN_GAP = 18
MESSAGE_X = NAME_X + round(max(len(r[1]) for r in RESULTS) * CHAR_WIDTH) + COLUMN_GAP
CARD_PAD = 15


def txt(
    x,
    y,
    s,
    fill,
    size=11,
    anchor="start",
    weight=None,
    font=None,
    preserve=False,
    spacing=None,
):
    a = f' font-weight="{weight}"' if weight else ""
    f = f' font-family="{font}"' if font else ""
    p = ' xml:space="preserve"' if preserve else ""
    ls = f' letter-spacing="{spacing}"' if spacing else ""
    return (
        f'  <text x="{x:g}" y="{y:g}" text-anchor="{anchor}" fill="{fill}" '
        f'font-size="{size}"{a}{f}{p}{ls}>{s}</text>'
    )


def arrow(t, x1, x2, y):
    return [
        f'  <line x1="{x1}" y1="{y}" x2="{x2 - 9}" y2="{y}" stroke="{t["step"]}" '
        f'stroke-width="2"/>',
        f'  <polygon points="{x2 - 9},{y - 5} {x2},{y} {x2 - 9},{y + 5}" '
        f'fill="{t["step"]}"/>',
    ]


def hero(t):
    x3, pw3 = STEPS[2][0], STEPS[2][1]
    w, top = x3 + pw3 + 20, 48
    # Top down: the rows, a rule, the summary line, then clear space.
    ry = top + 30
    fy = ry + len(RESULTS) * ROW_PITCH + 6
    card_bottom = fy + 18 + CARD_PAD
    ph = card_bottom + 10 - top
    h = top + ph + 36
    widest = MESSAGE_X + max(len(r[2]) for r in RESULTS) * CHAR_WIDTH
    if widest > pw3 - 10 - CARD_PAD:
        raise SystemExit(
            "a message runs past the report card, so widen the third entry of STEPS"
        )
    # The summary starts at the left of its line and the closing line ends at
    # the right, 26 from each side of the panel, with two characters between.
    if (len(SUMMARY) + len(CLOSING) + 2) * CHAR_WIDTH > pw3 - 2 * 26:
        raise SystemExit(
            "the summary runs into the closing line, so widen the third entry of STEPS"
        )
    s = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" '
        f'font-family="{SANS}" font-size="12" role="img" aria-labelledby="t d">',
        f'  <title id="t">{TITLE}</title>',
        f'  <desc id="d">{DESC}</desc>',
    ]
    if t["page"]:
        s.append(f'  <rect width="100%" height="100%" fill="{t["page"]}"/>')

    for i, (x, pw, label) in enumerate(STEPS):
        s += [
            f'  <circle cx="{x + 11}" cy="22" r="11" fill="{t["step"]}"/>',
            txt(
                x + 11, 26, str(i + 1), t["stepText"], 12, anchor="middle", weight="700"
            ),
            txt(x + 30, 27, label, t["text"], 11.5, weight="700", spacing="0.6"),
        ]
        s.append(
            f'  <rect x="{x}" y="{top}" width="{pw}" height="{ph}" rx="10" '
            f'fill="{t["panel"]}" stroke="{t["panelStroke"]}" stroke-width="1"/>'
        )

    # Step 1, the package as it arrives.
    x1 = STEPS[0][0]
    for i, (name, depth) in enumerate(TREE):
        col = t["dir"] if name.endswith("/") else t["text"]
        s.append(txt(x1 + 14 + depth * 12, top + 26 + i * 18, name, col, 10, font=MONO))
    s += [
        txt(x1 + 14, top + ph - 66, "Fetched, never installed.", t["body"], 9.5),
        txt(x1 + 14, top + ph - 50, "You do not intend to run", t["body"], 9.5),
        txt(x1 + 14, top + ph - 36, "a line of it.", t["body"], 9.5),
    ]

    # Step 2, the command.
    x2, pw2 = STEPS[1][0], STEPS[1][1]
    s += [
        f'  <rect x="{x2 + 10}" y="{top + 12}" width="{pw2 - 20}" height="60" rx="7" '
        f'fill="{t["card"]}" stroke="{t["panelStroke"]}" stroke-width="1"/>',
        txt(x2 + 20, top + 32, "$ pycmdcheck ./riverplot", t["text"], 10, font=MONO),
        txt(
            x2 + 30,
            top + 50,
            "--profile triage",
            t["accent"],
            10,
            font=MONO,
            weight="700",
        ),
        txt(x2 + 14, top + 96, "The triage profile runs", t["body"], 9.5),
        txt(x2 + 14, top + 110, "only the checks that read", t["body"], 9.5),
        txt(x2 + 14, top + 124, "files.", t["body"], 9.5),
        txt(x2 + 14, top + 152, "Nothing is built.", t["text"], 9.5, weight="700"),
        txt(x2 + 14, top + 168, "Nothing is imported.", t["text"], 9.5, weight="700"),
        txt(x2 + 14, top + 184, "Nothing reaches the", t["text"], 9.5, weight="700"),
        txt(x2 + 14, top + 198, "network.", t["text"], 9.5, weight="700"),
    ]

    # Step 3, the real report.
    s.append(
        f'  <rect x="{x3 + 10}" y="{top + 10}" width="{pw3 - 20}" '
        f'height="{ph - 20}" rx="7" fill="{t["card"]}" '
        f'stroke="{t["panelStroke"]}" stroke-width="1"/>'
    )
    for i, (glyph, name, msg) in enumerate(RESULTS):
        y = ry + i * ROW_PITCH
        s += [
            txt(
                x3 + 26,
                y,
                glyph,
                t[GLYPH_COLOR[glyph]],
                11,
                anchor="middle",
                weight="700",
            ),
            txt(x3 + NAME_X, y, name, t["text"], ROW_SIZE, font=MONO),
            txt(x3 + MESSAGE_X, y, msg, t["body"], ROW_SIZE, font=MONO),
        ]
    s += [
        f'  <line x1="{x3 + 20}" y1="{fy}" x2="{x3 + pw3 - 20}" y2="{fy}" '
        f'stroke="{t["rule"]}" stroke-width="1"/>',
        txt(x3 + 26, fy + 18, SUMMARY, t["body"], ROW_SIZE, font=MONO),
        txt(
            x3 + pw3 - 26,
            fy + 18,
            CLOSING,
            t[CLOSING_COLOR],
            ROW_SIZE,
            font=MONO,
            weight="700",
            anchor="end",
        ),
    ]

    # Each arrow sits in the space between two panels and touches neither.
    mid = top + ph / 2
    s += arrow(t, STEPS[0][0] + STEPS[0][1] + ARROW_GAP, STEPS[1][0] - ARROW_GAP, mid)
    s += arrow(t, STEPS[1][0] + STEPS[1][1] + ARROW_GAP, STEPS[2][0] - ARROW_GAP, mid)

    s.append(
        txt(
            w / 2,
            h - 10,
            "Output of pycmdcheck --profile triage on pycmdcheck's own repository. "
            "Twelve checks, no installation.",
            t["faint"],
            9.5,
            anchor="middle",
        )
    )
    s.append("</svg>")
    return "\n".join(s) + "\n"


ASSETS.mkdir(parents=True, exist_ok=True)
(ASSETS / "hero-light.svg").write_text(hero(LIGHT), encoding="utf-8")
(ASSETS / "hero-dark.svg").write_text(hero(DARK), encoding="utf-8")
print(f"wrote {ASSETS / 'hero-light.svg'}")
print(f"wrote {ASSETS / 'hero-dark.svg'}")
