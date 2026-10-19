"""Library keys: an embedded symbol is known by its full lib_id, never by its bare name.

KiCad draws a placed symbol from the lib_symbols entry named by its lib_name, else its lib_id
(sch_symbol.cpp GetSchSymbolLibraryName), and embeds a symbol it places under ``nickname:name``
(sch_screen.cpp Append). So ``A:R`` and ``B:R`` are two entries, and a tool that matches on the
part after the colon reads one library's pins for the other.

The libraries here are written by this module, so their pin coordinates are the judge: A's R has
vertical pins and no names, B's R has horizontal pins named IN and OUT.
"""

from __future__ import annotations

import re
from pathlib import Path

import edit_judge as J
import pytest
from conftest import HAS_KICAD_CLI, _cli_report
from mcp.server.mcpserver.exceptions import ToolError
from routing_fixtures import custom_lib, fresh, place

from mcp_server_kicad import _cst, schematic

A_PINS = [
    ("1", "~", "passive", 0, 3.81, 270, False),
    ("2", "~", "passive", 0, -3.81, 90, False),
]
B_PINS = [
    ("1", "IN", "passive", -5.08, 0, 0, False),
    ("2", "OUT", "passive", 5.08, 0, 180, False),
]


def _library(tmp_path: Path, folder: str, pins) -> str:
    """A one-symbol library file holding R with ``pins``, in its own folder."""
    d = tmp_path / folder
    d.mkdir()
    return custom_lib(d, "R", pins)


def _entry_names(path: str) -> list[str]:
    root = _cst.parse(Path(path).read_bytes()).lists[0]
    return [s.atoms[1].text for s in root.find("lib_symbols").find_all("symbol")]


def _embed(path: str, library: str, key: str) -> None:
    """Splice library's R into the sheet's lib_symbols under ``key``, as a hand edit or an
    older release would: a copy of the library file's node with the name atom retexted."""
    tree = _cst.parse(Path(path).read_bytes())
    root = tree.lists[0]
    node = next(
        s for s in _cst.parse(Path(library).read_bytes()).lists[0].find_all("symbol")
    ).copy()
    node.atoms[1].set_text(key)
    libs = root.find("lib_symbols")
    syms = libs.find_all("symbol")
    if syms:
        libs.insert_after(syms[-1], node)
    else:
        libs.append_child(node, b"\n\t\t")
    Path(path).write_bytes(_cst.serialize(tree))


def _three_libraries(tmp_path: Path) -> tuple[str, str, str]:
    """A sheet whose lib_symbols hold Device:R, A:R and B:R, and the A and B library files."""
    path = fresh(tmp_path)
    device = _library(tmp_path, "libdevice", A_PINS)
    a = _library(tmp_path, "liba", A_PINS)
    b = _library(tmp_path, "libb", B_PINS)
    _embed(path, device, "Device:R")
    _embed(path, a, "A:R")
    _embed(path, b, "B:R")
    assert _entry_names(path) == ["Device:R", "A:R", "B:R"]
    return path, a, b


def _pins(result: str) -> dict[str, tuple[str, float, float]]:
    """get_pin_positions' ``  Pin N (name): (x, y)`` lines as {N: (name, x, y)}."""
    return {
        m["n"]: (m["name"], float(m["x"]), float(m["y"]))
        for m in re.finditer(
            r"Pin (?P<n>\S+) \((?P<name>[^)]*)\): \((?P<x>\S+), (?P<y>\S+)\)", result
        )
    }


def _place(path: str, lib_id: str, ref: str, x: float, library: str) -> None:
    schematic.place_component(
        lib_id=lib_id,
        reference=ref,
        value=ref,
        x=x,
        y=101.6,
        symbol_lib_path=library,
        schematic_path=path,
    )


def test_place_component_draws_its_own_librarys_pins(tmp_path):
    """A:R then B:R from two libraries that both call their symbol R: U2 sits at (152.4,
    101.6) and its pins are B's, IN 5.08 mm left of the origin and OUT 5.08 mm right."""
    path = fresh(tmp_path)
    a, b = _library(tmp_path, "liba", A_PINS), _library(tmp_path, "libb", B_PINS)
    _place(path, "A:R", "U1", 101.6, a)
    _place(path, "B:R", "U2", 152.4, b)
    assert _pins(schematic.get_pin_positions("U2", schematic_path=path)) == {
        "1": ("IN", 147.32, 101.6),
        "2": ("OUT", 157.48, 101.6),
    }
    assert _pins(schematic.get_pin_positions("U1", schematic_path=path)) == {
        "1": ("~", 101.6, 97.79),
        "2": ("~", 101.6, 105.41),
    }
    assert _entry_names(path) == ["A:R", "B:R"]


@pytest.mark.skipif(not HAS_KICAD_CLI, reason="kicad-cli not found")
def test_same_named_symbols_keep_their_pins_in_kicad_cli(tmp_path):
    """The same two placements, read by kicad-cli's netlist: U2's pins carry the functions IN
    and OUT that B's library gives them, which a placement that drew A's pins would lose."""
    path = fresh(tmp_path)
    a, b = _library(tmp_path, "liba", A_PINS), _library(tmp_path, "libb", B_PINS)
    _place(path, "A:R", "U1", 101.6, a)
    _place(path, "B:R", "U2", 152.4, b)
    _, xml = _cli_report(["sch", "export", "netlist", "--format", "kicadxml"], Path(path), "n.xml")
    assert xml is not None, "kicad-cli exported no netlist"
    functions = {
        (m["ref"], m["pin"]): m["fn"]
        for m in re.finditer(
            r'<node ref="(?P<ref>[^"]*)" pin="(?P<pin>[^"]*)"(?: pinfunction="(?P<fn>[^"]*)")?',
            xml.decode("utf-8", "replace"),
        )
        if m["fn"] is not None
    }
    # kicad-cli 10.0.7 prints the function with the pin number appended (IN_1), measured
    # 2026-10-19; kicad-cli 9.0.8 prints IN.
    wanted = {("U2", "1"): "IN", ("U2", "2"): "OUT"}
    got = {k: v for k, v in functions.items() if k[0] == "U2"}
    assert {k: v.removesuffix(f"_{k[1]}") for k, v in got.items()} == wanted, functions


def test_a_symbol_lib_path_definition_is_embedded_under_the_lib_id(tmp_path):
    """Placing A:R from a library file inserts the placed symbol and one lib_symbols entry,
    and the entry is named A:R."""
    path = fresh(tmp_path)
    library = _library(tmp_path, "liba", A_PINS)
    before = Path(path).read_bytes()
    _place(path, "A:R", "U1", 101.6, library)
    J.assert_inserted(before, Path(path).read_bytes(), heads=["symbol", "symbol"], contiguous=False)
    assert _entry_names(path) == ["A:R"]


def test_get_symbol_pins_reads_the_library_it_names(tmp_path):
    """On a sheet holding Device:R, A:R and B:R each full name shows its own library's pins."""
    path, _a, _b = _three_libraries(tmp_path)
    b = schematic.get_symbol_pins("B:R", schematic_path=path)
    assert b.startswith("Symbol: B:R")
    assert "IN" in b and "OUT" in b and "rot=0" in b and "rot=180" in b
    a = schematic.get_symbol_pins("A:R", schematic_path=path)
    assert "IN" not in a and "rot=270" in a
    device = schematic.get_symbol_pins("Device:R", schematic_path=path)
    assert "IN" not in device and "rot=270" in device


def test_get_symbol_pins_by_a_name_two_entries_share_is_refused(tmp_path):
    """R ends Device:R, A:R and B:R: the error names the three and asks for the full name."""
    path, _a, _b = _three_libraries(tmp_path)
    with pytest.raises(ToolError) as refused:
        schematic.get_symbol_pins("R", schematic_path=path)
    for key in ("Device:R", "A:R", "B:R"):
        assert key in str(refused.value)


def test_get_symbol_pins_by_a_bare_name_one_entry_ends_is_accepted(tmp_path):
    """With one entry ending in R, the bare name finds it."""
    path = fresh(tmp_path)
    place(path, "R", "R1", 101.6, 101.6)
    result = schematic.get_symbol_pins("R", schematic_path=path)
    assert result.startswith("Symbol: R") and "Pin 1" in result


def test_add_lib_symbol_checks_the_name_it_embeds_under(tmp_path):
    """No entry of the sheet is named R (they are Device:R, A:R and B:R), so add_lib_symbol
    embeds one under the bare name it was given, and a second call is refused."""
    path, a, _b = _three_libraries(tmp_path)
    before = Path(path).read_bytes()
    schematic.add_lib_symbol(a, "R", path)
    J.assert_inserted(before, Path(path).read_bytes(), heads=["symbol"])
    assert _entry_names(path) == ["Device:R", "A:R", "B:R", "R"]
    after = Path(path).read_bytes()
    with pytest.raises(ToolError, match="already in lib_symbols"):
        schematic.add_lib_symbol(a, "R", path)
    assert Path(path).read_bytes() == after
