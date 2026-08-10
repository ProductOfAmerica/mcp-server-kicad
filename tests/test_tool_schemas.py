"""What each tool publishes to the client: inputSchema, outputSchema, title.

The published inputSchema must describe the keys a tool actually requires.

Nine parameters used to publish `{"items": {"type": "object",
"additionalProperties": true}}`, which tells a model nothing: add_wires needs
x1/y1/x2/y2 and indexed them straight out of the dict, so a wrong key came back
as a bare KeyError after a round trip.
"""

import inspect
from types import UnionType
from typing import Union, get_origin

from mcp_server_kicad import footprint, pcb, project, schematic, symbol

_MODULES = [footprint, pcb, project, schematic, symbol]


def _all_tools():
    for mod in _MODULES:
        yield from mod.mcp._tool_manager._tools.values()


def _array_items(schema: dict, defs: dict) -> dict | None:
    """The item schema of an array parameter, with $ref and Optional resolved."""
    for branch in [schema, *(schema.get("anyOf") or [])]:
        if branch.get("type") != "array":
            continue
        items = branch.get("items") or {}
        ref = items.get("$ref")
        if ref:
            return defs.get(ref.rsplit("/", 1)[-1], {})
        return items
    return None


def _object_list_params() -> list[tuple[str, str, dict]]:
    """(tool, parameter, item schema) for every parameter taking a list of objects."""
    found = []
    for tool in _all_tools():
        defs = tool.parameters.get("$defs", {})
        for name, prop in tool.parameters.get("properties", {}).items():
            items = _array_items(prop, defs)
            if items and items.get("type") == "object":
                found.append((tool.name, name, items))
    return found


def test_object_list_parameters_declare_their_required_keys():
    """Swept rather than listed, so a tenth such parameter is covered too."""
    loose = [
        f"{tool}.{param}"
        for tool, param, items in _object_list_params()
        if not items.get("properties") or not items.get("required")
    ]
    assert loose == [], f"list parameters with no key contract: {loose}"


def test_the_sweep_sees_the_anyOf_wrapped_parameter():
    """Guards the guard: an empty sweep must not read as a clean surface.

    add_symbol's `rectangles` is `list[RectangleSpec] | None`, so its array sits
    inside an anyOf wrapper. A helper that reads only the top level finds no
    array there and reports nothing, which is indistinguishable from a pass.
    """
    assert ("add_symbol", "rectangles") in [(t, p) for t, p, _ in _object_list_params()]


def _return_annotation(tool):
    """The tool's real return type.

    eval_str is required: pcb.py and project.py use postponed annotations, so
    without it the annotation comes back as a string and the check below
    silently degrades to substring matching.
    """
    return inspect.signature(tool.fn, eval_str=True).return_annotation


def test_no_tool_returns_a_union():
    """The SDK wraps a union return in {"result": ...} and a bare model not.

    Three export tools returned unions, so reaching `path` meant branching on
    which export tool you had called. Keyed on the annotation rather than on
    the wrapper shape: 75 of the tools are legitimately wrapped because they
    return a str or a list, so a shape-only assertion is unusable here.
    """
    unions = [
        tool.name
        for tool in _all_tools()
        if get_origin(_return_annotation(tool)) in (Union, UnionType)
    ]
    assert unions == [], f"tools returning a union, whose output gets wrapped: {unions}"


# Tools with a near neighbour on the surface: the same verb on a different
# subject, or the same subject read at a different stage. A client shows `name`
# when there is no title, so these are the ones where the raw function name
# leaves a model picking from description prose alone.
_NEEDS_A_TITLE = {
    "get_symbol_info",
    "get_symbol_pins",
    "get_footprint_info",
    "get_footprint_pads",
    "get_footprint_bounds",
    "export_netlist",
    "export_hierarchical_netlist",
    "validate_board",
    "check_placement",
}


def test_ambiguous_tools_carry_distinct_titles():
    titled = {tool.name: tool.title for tool in _all_tools() if tool.name in _NEEDS_A_TITLE}
    assert set(titled) == _NEEDS_A_TITLE, (
        f"renamed or removed since the titles were chosen: {_NEEDS_A_TITLE ^ set(titled)}"
    )
    untitled = sorted(name for name, title in titled.items() if not title)
    assert untitled == [], f"ambiguous tools with no title: {untitled}"
    assert len(set(titled.values())) == len(titled), f"titles are not distinct: {titled}"
