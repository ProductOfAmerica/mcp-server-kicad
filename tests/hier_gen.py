"""A project whose sheet hierarchy has plain, reused, nested and subdirectory sheets.

Built with the package's own project tools and then rearranged the way a KiCad user lays out
a project: sheet files moved into folders, each Sheetfile naming its file relative to the
sheet that holds the block, which is how KiCad resolves it (SCH_IO_KICAD_SEXPR::loadHierarchy,
eeschema/sch_io/kicad_sexpr/sch_io_kicad_sexpr.cpp:178-199 at 9.0.8). kicad-cli judges the
result in the tests that use it; nothing here is the oracle.

    proj.kicad_sch            root
      Power  power.kicad_sch  one unwired resistor, a dangling label and wire
      Ch1    chan.kicad_sch   one unwired resistor and a dangling wire
      Ch2    chan.kicad_sch   the same file again (a reused sheet)
      Mid    sch/mid.kicad_sch
        Deep sch/deep/deep.kicad_sch   one unwired resistor, named from mid.kicad_sch
      Sub    sub/sub.kicad_sch         one unwired resistor and a hierarchical label on a sheet pin
    stray.kicad_sch           in the project's folder, used by no sheet; one unwired resistor
    sch/stray2.kicad_sch      in a subfolder, used by no sheet

Every sheet file has a name of its own, because the netlist names a sheet's file by the
file's name alone.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from mcp_server_kicad import project, schematic


@dataclass(frozen=True)
class Hierarchy:
    folder: Path
    pro: Path
    root: Path
    power: Path
    chan: Path
    mid: Path
    deep: Path
    sub: Path
    stray: Path
    stray2: Path

    @property
    def sheets(self) -> dict[str, Path]:
        """Every sheet file the root's hierarchy uses, by file name."""
        files = (self.power, self.chan, self.mid, self.deep, self.sub)
        return {f.name: f for f in files}

    @property
    def unused(self) -> tuple[Path, Path]:
        return self.stray, self.stray2


def _point_sheet_at(parent: Path, flat: str, relative: str) -> None:
    """Point the sheet block whose Sheetfile is *flat* at *relative*; no other byte changes."""
    data = parent.read_bytes()
    old = f'(property "Sheetfile" "{flat}"'.encode()
    assert data.count(old) >= 1, (parent, flat)
    parent.write_bytes(data.replace(old, f'(property "Sheetfile" "{relative}"'.encode()))


def build(tmp_path: Path) -> Hierarchy:
    folder = tmp_path / "proj"
    project.create_project(directory=str(folder), name="proj")
    pro, root = folder / "proj.kicad_pro", folder / "proj.kicad_sch"
    names = ("power", "chan", "mid", "deep", "sub", "stray", "stray2")
    files = {n: folder / f"{n}.kicad_sch" for n in names}
    for f in files.values():
        project.create_schematic(schematic_path=str(f))

    def sheet(parent: Path, name: str, child: Path, x: float, pins=()) -> None:
        project.add_hierarchical_sheet(
            parent_schematic_path=str(parent),
            sheet_name=name,
            sheet_file=str(child),
            pins=list(pins),
            project_path=str(pro),
            x=x,
            y=50.8,
        )

    sheet(root, "Power", files["power"], 25.4)
    sheet(root, "Ch1", files["chan"], 76.2)
    sheet(root, "Ch2", files["chan"], 127.0)
    sheet(root, "Mid", files["mid"], 177.8)
    sheet(files["mid"], "Deep", files["deep"], 25.4)
    sheet(root, "Sub", files["sub"], 228.6, pins=[{"name": "VIN", "direction": "input"}])

    for name, ref in (
        ("power", "R1"),
        ("chan", "R2"),
        ("deep", "R3"),
        ("sub", "R4"),
        ("stray", "R5"),
    ):
        schematic.place_component(
            "Device:R",
            ref,
            "10K",
            50.8,
            50.8,
            schematic_path=str(files[name]),
            project_path=str(pro),
        )
    for name in ("power", "chan"):
        schematic.add_wires(
            [{"x1": 127.0, "y1": 25.4, "x2": 152.4, "y2": 25.4}], schematic_path=str(files[name])
        )
    schematic.add_label("FLOAT", 60.96, 30.48, schematic_path=str(files["power"]))

    # lay the files out in folders, and tell each parent where its sheet now is
    for sub in ("sch", "sch/deep", "sub"):
        (folder / sub).mkdir(parents=True, exist_ok=True)
    moved = {
        "mid": "sch/mid.kicad_sch",
        "deep": "sch/deep/deep.kicad_sch",
        "sub": "sub/sub.kicad_sch",
        "stray2": "sch/stray2.kicad_sch",
    }
    for name, relative in moved.items():
        files[name].rename(folder / relative)
        files[name] = folder / relative
    _point_sheet_at(root, "mid.kicad_sch", "sch/mid.kicad_sch")
    _point_sheet_at(root, "sub.kicad_sch", "sub/sub.kicad_sch")
    _point_sheet_at(files["mid"], "deep.kicad_sch", "deep/deep.kicad_sch")
    return Hierarchy(
        folder, pro, root, files["power"], files["chan"], files["mid"], files["deep"],
        files["sub"], files["stray"], files["stray2"],
    )  # fmt: skip
