"""Shared constants, helpers, and kiutils re-exports for KiCad MCP servers."""

import os
import subprocess
import uuid
from pathlib import Path

from kiutils.board import Board
from kiutils.footprint import Footprint, Pad
from kiutils.items.brditems import Segment, Via
from kiutils.items.common import ColorRGBA, Effects, Font, Net, Position, Property, Stroke
from kiutils.items.fpitems import FpText
from kiutils.items.gritems import GrLine, GrText
from kiutils.items.schitems import (
    Connection,
    GlobalLabel,
    Junction,
    LocalLabel,
    NoConnect,
    SchematicSymbol,
    SymbolProjectInstance,
    SymbolProjectPath,
    Text,
)
from kiutils.items.zones import Zone
from kiutils.schematic import Schematic
from kiutils.symbol import SymbolLib

# ---------------------------------------------------------------------------
# Re-exports (for convenient "from _shared import ..." in server modules)
# ---------------------------------------------------------------------------

__all__ = [
    # kiutils types
    "Board",
    "ColorRGBA",
    "Connection",
    "Effects",
    "Font",
    "Footprint",
    "FpText",
    "GlobalLabel",
    "GrLine",
    "GrText",
    "Junction",
    "LocalLabel",
    "Net",
    "NoConnect",
    "Pad",
    "Position",
    "Property",
    "Schematic",
    "SchematicSymbol",
    "SymbolProjectInstance",
    "SymbolProjectPath",
    "Segment",
    "Stroke",
    "SymbolLib",
    "Text",
    "Via",
    "Zone",
    # path constants
    "SCH_PATH",
    "SYM_LIB_PATH",
    "PCB_PATH",
    "FP_LIB_PATH",
    "OUTPUT_DIR",
    # helpers
    "_cwd",
    "_resolve_config",
    "_load_sch",
    "_load_board",
    "_gen_uuid",
    "_default_effects",
    "_default_stroke",
    "_run_cli",
    "_file_meta",
    "_fp_ref",
    "_fp_val",
]


# ---------------------------------------------------------------------------
# Config resolution
# ---------------------------------------------------------------------------


def _cwd() -> Path:
    """Return the current working directory. Wrapped for test mockability."""
    return Path.cwd()


def _resolve_config() -> dict[str, str]:
    """Resolve KiCad project paths with the following priority:

    1. Auto-detect: scan cwd for ``*.kicad_pro``. If exactly 1 found,
       derive sibling paths (.kicad_sch, .kicad_pcb, .kicad_sym, .pretty/).
    2. Env vars: ``KICAD_SCH_PATH``, ``KICAD_PCB_PATH``, ``KICAD_SYM_LIB``,
       ``KICAD_FP_LIB``, ``KICAD_OUTPUT_DIR`` override auto-detected values.
    3. Empty default: if neither source provides a value, the path is "".
    """
    cfg: dict[str, str] = {
        "sch_path": "",
        "pcb_path": "",
        "sym_lib_path": "",
        "fp_lib_path": "",
        "output_dir": "",
    }

    # --- Step 1: auto-detect from cwd ---
    cwd = _cwd()
    pro_files = list(cwd.glob("*.kicad_pro"))

    if len(pro_files) == 1:
        stem = pro_files[0].stem

        sch = cwd / f"{stem}.kicad_sch"
        if sch.exists():
            cfg["sch_path"] = str(sch)

        pcb = cwd / f"{stem}.kicad_pcb"
        if pcb.exists():
            cfg["pcb_path"] = str(pcb)

        sym = cwd / f"{stem}.kicad_sym"
        if sym.exists():
            cfg["sym_lib_path"] = str(sym)

        pretty = cwd / f"{stem}.pretty"
        if pretty.is_dir():
            cfg["fp_lib_path"] = str(pretty)

        # output_dir is always the project directory when a project is detected
        cfg["output_dir"] = str(cwd)

    # --- Step 2: env var overrides ---
    env_map = {
        "sch_path": "KICAD_SCH_PATH",
        "pcb_path": "KICAD_PCB_PATH",
        "sym_lib_path": "KICAD_SYM_LIB",
        "fp_lib_path": "KICAD_FP_LIB",
        "output_dir": "KICAD_OUTPUT_DIR",
    }
    for key, env_var in env_map.items():
        val = os.environ.get(env_var)
        if val:
            cfg[key] = val

    return cfg


# ---------------------------------------------------------------------------
# Module-level path constants (set from _resolve_config at import time)
# ---------------------------------------------------------------------------

_cfg = _resolve_config()
SCH_PATH: str = _cfg["sch_path"]
SYM_LIB_PATH: str = _cfg["sym_lib_path"]
PCB_PATH: str = _cfg["pcb_path"]
FP_LIB_PATH: str = _cfg["fp_lib_path"]
OUTPUT_DIR: str = _cfg["output_dir"]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_sch(path: str = SCH_PATH) -> Schematic:
    """Load a KiCad schematic from *path*."""
    if not path:
        raise ValueError("No schematic path provided. Pass sch_path parameter.")
    return Schematic.from_file(path)


def _gen_uuid() -> str:
    return str(uuid.uuid4())


def _default_effects(size: float = 1.27) -> Effects:
    return Effects(font=Font(height=size, width=size))


def _default_stroke() -> Stroke:
    return Stroke(width=0, type="default")


def _load_board(path: str = PCB_PATH) -> Board:
    """Load a KiCad PCB from *path*."""
    if not path:
        raise ValueError("No PCB path provided. Pass pcb_path parameter.")
    return Board.from_file(path)


def _run_cli(args: list[str], check: bool = True) -> subprocess.CompletedProcess:
    """Run a kicad-cli command, return CompletedProcess."""
    result = subprocess.run(
        ["kicad-cli"] + args,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"kicad-cli failed: {result.stderr.strip()}")
    return result


def _file_meta(path: str) -> dict:
    """Return basic file metadata."""
    p = Path(path)
    return {"path": str(p.resolve()), "size_bytes": p.stat().st_size}


def _fp_ref(fp: Footprint) -> str:
    """Extract the reference designator from a footprint."""
    if "Reference" in fp.properties:
        return fp.properties["Reference"]
    for item in fp.graphicItems:
        if isinstance(item, FpText) and item.type == "reference":
            return item.text
    return "?"


def _fp_val(fp: Footprint) -> str:
    """Extract the value from a footprint."""
    if "Value" in fp.properties:
        return fp.properties["Value"]
    for item in fp.graphicItems:
        if isinstance(item, FpText) and item.type == "value":
            return item.text
    return "?"
