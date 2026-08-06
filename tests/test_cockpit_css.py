"""Cockpit stylesheet guards for the Results grid's row states (#491).

Tabulator ships a light theme: it paints the table body white and its text
`#333`. `cockpit.css` covers that with opaque dark rows — which works right up
until a row state is written as a translucent tint, because a tint *replaces*
the row's opaque background instead of layering over it. The vendor white then
bleeds through under near-white cell text, and the row you just selected is the
one row you cannot read. These are the two invariants that stop it recurring.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
# Comments go first: a `/* … */` above a rule would otherwise be read as part of
# its selector (and this stylesheet documents nearly every rule).
CSS = re.sub(
    r"/\*.*?\*/", "", (REPO_ROOT / "docs" / "cockpit.css").read_text(encoding="utf-8"), flags=re.S
)

TABLE_BODY = ".tabulator .tabulator-tableholder .tabulator-table"
ROW_STATES = (
    ".tabulator .tabulator-row:hover",
    ".tabulator .tabulator-row.rs-sel",
    ".tabulator .tabulator-row.rs-sel:hover",
)


def declarations(selector: str) -> dict[str, str]:
    """Merge every declaration block written against exactly `selector`."""
    out: dict[str, str] = {}
    for sels, body in re.findall(r"([^{}]*)\{([^{}]*)\}", CSS):
        if selector not in [s.strip() for s in sels.replace("\n", " ").split(",")]:
            continue
        for decl in body.split(";"):
            prop, _, value = decl.partition(":")
            if value:
                out[prop.strip()] = value.strip()
    return out


def resolve(value: str) -> str:
    """Substitute a single `var(--name)` with its `:root` value."""
    name = re.fullmatch(r"var\((--[\w-]+)\)", value)
    if not name:
        return value
    root = declarations(":root")
    assert name[1] in root, f"{value} is not defined in :root"
    return root[name[1]]


@pytest.mark.parametrize("selector", ROW_STATES)
def test_row_state_backgrounds_are_opaque(selector: str) -> None:
    decls = declarations(selector)
    assert "background" in decls, f"{selector} no longer sets a background"
    colour = resolve(decls["background"].split()[0])
    assert re.fullmatch(r"#[0-9a-fA-F]{6}", colour), (
        f"{selector} paints {colour} — a row state must be an opaque colour, or "
        f"whatever sits behind the rows shows through it (#491)"
    )


def test_vendor_white_table_body_is_neutralised() -> None:
    """Belt-and-braces: the grid's backdrop must never be able to render white."""
    decls = declarations(TABLE_BODY)
    assert decls.get("background-color") == "transparent", (
        f"{TABLE_BODY} must clear Tabulator's white body — it is the white that "
        f"bleeds through anything translucent above it (#491)"
    )
    assert decls.get("color") == "var(--ink)", (
        f"{TABLE_BODY} must clear Tabulator's #333 text — a cell that does not "
        f"set its own colour would inherit near-black onto the dark grid"
    )
