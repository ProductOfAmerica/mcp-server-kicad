"""The published inputSchema must describe the keys a tool actually requires.

Nine parameters used to publish `{"items": {"type": "object",
"additionalProperties": true}}`, which tells a model nothing: add_wires needs
x1/y1/x2/y2 and indexed them straight out of the dict, so a wrong key came back
as a bare KeyError after a round trip.

Every helper here resolves through `anyOf`. An optional parameter such as
`list[X] | None` publishes its array under an `anyOf` wrapper with a null
branch, so a check that reads the top level passes on it no matter what it
contains. That blind spot hid add_symbol's `rectangles` from a first draft of
this file.
"""

import pytest

from mcp_server_kicad import footprint, pcb, project, schematic, symbol

_MODULES = {
    "footprint": footprint,
    "pcb": pcb,
    "project": project,
    "schematic": schematic,
    "symbol": symbol,
}

# (tool, parameter) pairs that take a list of structured entries.
_LIST_PARAMS = [
    ("pcb", "add_copper_zone", "corners"),
    ("pcb", "add_keepout_zone", "corners"),
    ("project", "create_sym_lib_table", "entries"),
    ("project", "add_hierarchical_sheet", "pins"),
    ("schematic", "add_wires", "wires"),
    ("schematic", "add_junctions", "points"),
    ("schematic", "wire_pins_to_net", "pins"),
    ("symbol", "add_symbol", "pins"),
    ("symbol", "add_symbol", "rectangles"),
]


def _all_tools():
    for mod in _MODULES.values():
        yield from mod.mcp._tool_manager._tools.values()


def _branches(schema: dict) -> list[dict]:
    """A schema plus any anyOf branches, so Optional wrappers are seen through."""
    return [schema] + list(schema.get("anyOf") or [])


def _array_items(schema: dict, defs: dict) -> dict | None:
    """The item schema of an array parameter, with any $ref resolved."""
    for branch in _branches(schema):
        if branch.get("type") != "array":
            continue
        items = branch.get("items") or {}
        ref = items.get("$ref")
        if ref:
            return defs.get(ref.rsplit("/", 1)[-1], {})
        return items
    return None


@pytest.mark.parametrize("mod,tool,param", _LIST_PARAMS)
def test_list_parameter_declares_its_required_keys(mod, tool, param):
    schema = _MODULES[mod].mcp._tool_manager._tools[tool].parameters
    items = _array_items(schema["properties"][param], schema.get("$defs", {}))
    assert items is not None, f"{tool}.{param} is not an array parameter"
    assert items.get("properties"), f"{tool}.{param} items declare no properties"
    assert items.get("required"), f"{tool}.{param} items declare no required keys"


def test_no_parameter_accepts_an_unconstrained_object_list():
    """Surface-wide sweep, so a new list[dict] cannot be added unnoticed."""
    loose = []
    for tool in _all_tools():
        for name, prop in tool.parameters.get("properties", {}).items():
            for branch in _branches(prop):
                if branch.get("type") == "array" and (branch.get("items") or {}).get(
                    "additionalProperties"
                ):
                    loose.append(f"{tool.name}.{name}")
    assert loose == [], f"list parameters with no key contract: {loose}"


def test_the_sweep_sees_through_anyOf():
    """Guards the guard: the wrapper that hid `rectangles` must not hide again."""
    wrapped = {
        "anyOf": [
            {"type": "array", "items": {"type": "object", "additionalProperties": True}},
            {"type": "null"},
        ]
    }
    assert _array_items(wrapped, {}) == {"type": "object", "additionalProperties": True}
    assert any(b.get("type") == "array" for b in _branches(wrapped)), (
        "a flat read of this schema sees no array at all"
    )
