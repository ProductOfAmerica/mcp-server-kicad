"""Shared constants and helpers for KiCad MCP servers."""

import math
import os
import re
import shutil
import subprocess
import uuid
from functools import lru_cache
from pathlib import Path

from kiutils.board import Board
from kiutils.footprint import Footprint
from kiutils.items.common import Effects, Font, Position, Stroke
from kiutils.items.fpitems import FpArc, FpCircle, FpLine, FpPoly, FpRect, FpText
from kiutils.items.gritems import GrArc, GrLine
from kiutils.items.zones import Hatch, KeepoutSettings, Zone, ZonePolygon
from kiutils.schematic import Schematic
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

import mcp_server_kicad._cst as _cst

# ---------------------------------------------------------------------------
# Tool annotation presets
# ---------------------------------------------------------------------------

_READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)

_ADDITIVE = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=False,
)

_DESTRUCTIVE = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=True,
    idempotentHint=False,
    openWorldHint=False,
)

# Every tool carrying _EXPORT writes an output file, so it is not read-only.
_EXPORT = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)


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
# Format version guard
# ---------------------------------------------------------------------------

# Read-max format versions: what KiCad 9 stable writes per file family. These
# are distinct from the write constants in project.py/symbol.py. Newer files
# are refused before parsing: kiutils 1.4.8 crashes on KiCad 10 boards and
# silently rewrites KiCad 10 schematics into its 2021 dialect (measured in #9).
_FORMAT_VERSION_LIMITS = {
    "kicad_sch": 20250114,
    "kicad_pcb": 20241229,
    "kicad_symbol_lib": 20241209,
    "footprint": 20241229,
}
_ROOT_TOKEN_RE = re.compile(r"^\s*\(\s*([a-z_0-9]+)")
_FILE_VERSION_RE = re.compile(r"\(version\s+(\d+)\s*\)")


def _check_format_version(path: str, head: str | None = None) -> None:
    """Refuse to load *path* if its format version is newer than KiCad 9.

    Callers that already read the file pass its text as *head*; otherwise only
    the first 512 bytes are read. Files with no version token (pre-KiCad-6)
    and non-KiCad files pass through so downstream behavior is unchanged.
    """
    if head is None:
        try:
            with open(path, "rb") as f:
                head = f.read(512).decode("utf-8", errors="ignore")
        except OSError:
            return  # missing/unreadable: keep today's downstream error
    else:
        head = head[:512]
    root = _ROOT_TOKEN_RE.match(head)
    if not root:
        return
    limit = _FORMAT_VERSION_LIMITS.get(root.group(1))
    if limit is None:
        return
    version = _FILE_VERSION_RE.search(head)
    if not version:
        return
    found = int(version.group(1))
    if found <= limit:
        return
    # KiCad 10 installs ship stock libraries already in newer formats
    # (Device.kicad_sym at 20251024). We only ever read those, and refusing
    # them would break system-symbol placement, so anything under a KiCad
    # install or configured symbol dir is exempt. On a Linux system install
    # _kicad_root() is /usr, so the exemption is deliberately loose.
    resolved = Path(path).resolve()
    system_dirs = list(_SYSTEM_SYM_DIRS)
    install_root = _kicad_root()
    if install_root is not None:
        system_dirs.append(install_root)
    env_dir = os.environ.get("KICAD_SYMBOL_DIR")
    if env_dir:
        system_dirs.append(Path(env_dir))
    if any(resolved.is_relative_to(d.resolve()) for d in system_dirs):
        return
    raise ToolError(
        f"{Path(path).name}: format version {found} is newer than the KiCad 9 "
        f"formats this server supports ({root.group(1)} <= {limit}). Loading "
        "would silently drop KiCad 10+ data and a save could corrupt the "
        "file, so it is refused. Keep editing this file in KiCad 10+; parser "
        "upgrade tracked in #9."
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_sch(path: str = SCH_PATH) -> Schematic:
    """Load a KiCad schematic from *path*."""
    if not path:
        raise ValueError("No schematic path provided. Pass sch_path parameter.")
    _check_format_version(path)
    sch = Schematic.from_file(path)
    # Cache raw system lib symbol text to prevent kiutils round-trip corruption.
    # lib_symbols inside schematics have libraryNickname=None, so we build a
    # mapping from schematicSymbols' libId (which has "Library:Symbol" format).
    _sym_to_lib: dict[str, str] = {}
    for sym in sch.schematicSymbols:
        lib_id = sym.libId or ""
        if ":" in lib_id:
            prefix, name = lib_id.split(":", 1)
            if name not in _sym_to_lib:
                _sym_to_lib[name] = prefix
    for lib_sym in sch.libSymbols:
        sym_name = lib_sym.entryName
        if sym_name not in _RAW_LIB_SYMBOLS:
            lib_prefix = _sym_to_lib.get(sym_name, "")
            lib_path = _resolve_system_lib(lib_prefix) if lib_prefix else None
            if lib_path:
                raw = _extract_raw_symbol(lib_path, sym_name)
                if raw:
                    _RAW_LIB_SYMBOLS[sym_name] = raw
    return sch


def _gen_uuid() -> str:
    return str(uuid.uuid4())


def _default_effects(size: float = 1.27) -> Effects:
    return Effects(font=Font(height=size, width=size))


def _default_stroke() -> Stroke:
    return Stroke(width=0, type="default")


# Default KiCad grid spacing in mm (50 mils).
_GRID_MM = 1.27


def _snap_grid(val: float, grid: float = _GRID_MM) -> float:
    """Snap *val* to the nearest multiple of *grid*."""
    return round(round(val / grid) * grid, 4)


def _sym_property_cst(node, key: str) -> str | None:
    """Value of a CST (property "key" "value") child, or None."""
    for p in node.find_all("property"):
        if p.atoms[1].text == key:
            return p.atoms[2].text
    return None


def _node_uuid(node) -> str:
    u = node.find("uuid")
    return u.atoms[1].text if u is not None else ""


def _sheet_file_cst(sheet) -> str | None:
    """Sheet file name; KiCad 9 writes "Sheetfile", kiutils "Sheet file"."""
    for key in ("Sheetfile", "Sheet file"):
        v = _sym_property_cst(sheet, key)
        if v is not None:
            return v
    return None


def _resolve_hierarchy_path(
    project_path: str, schematic_path: str, sch_uuid: str
) -> tuple[str, str]:
    """Derive the project name and full sheet-instance path for a schematic.

    Args:
        project_path: Path to the ``.kicad_pro`` file (used for project name
            and to locate the root schematic).
        schematic_path: Path to the ``.kicad_sch`` being edited.
        sch_uuid: UUID of the schematic being edited (already loaded by caller).

    Returns:
        ``(project_name, sheetInstancePath)`` tuple.  For the root schematic
        the path is ``/{root_uuid}``.  For a sub-sheet it is
        ``/{root_uuid}/{sheet_uuid}`` where *sheet_uuid* is the hierarchical
        sheet block's UUID in the parent.
    """
    pro = Path(project_path)
    project_name = pro.stem
    root_sch_path = pro.with_suffix(".kicad_sch")
    # Root schematic — simple case
    if Path(schematic_path).resolve() == root_sch_path.resolve():
        return project_name, f"/{sch_uuid}"

    # Sub-sheet — find its sheet block UUID in the root schematic (CST read,
    # so no version guard and no format restriction on the root)
    root = _cst.parse(root_sch_path.read_bytes()).lists[0]
    target_name = Path(schematic_path).name
    for sheet in root.find_all("sheet"):
        if _sheet_file_cst(sheet) == target_name:
            return project_name, f"/{_node_uuid(root)}/{_node_uuid(sheet)}"

    # Fallback: couldn't find sheet in root — use own UUID
    return project_name, f"/{sch_uuid}"


def _find_root_schematic(schematic_path: str) -> str | None:
    """Return the root schematic path if *schematic_path* is a sub-sheet.

    Looks for a ``.kicad_pro`` in the same directory and derives the root
    ``.kicad_sch`` from its stem.  Returns ``None`` when *schematic_path*
    is already the root (or no project file is found).
    """
    sch_dir = Path(schematic_path).parent
    pro_files = list(sch_dir.glob("*.kicad_pro"))
    if len(pro_files) != 1:
        return None
    root_sch = pro_files[0].with_suffix(".kicad_sch")
    if not root_sch.exists():
        return None
    if root_sch.resolve() == Path(schematic_path).resolve():
        return None
    return str(root_sch)


def _resolve_root(schematic_path: str, project_path: str = "") -> str | None:
    """Find the root schematic, preferring explicit project_path.

    Returns the root .kicad_sch path if schematic_path is a sub-sheet,
    or None if it IS the root (or no root can be determined).
    """
    if project_path:
        pro = Path(project_path)
        root_sch = pro.with_suffix(".kicad_sch")
        if root_sch.exists() and root_sch.resolve() != Path(schematic_path).resolve():
            return str(root_sch)
        return None
    return _find_root_schematic(schematic_path)


# ---------------------------------------------------------------------------
# System library resolution
# ---------------------------------------------------------------------------

_SYSTEM_SYM_DIRS: list[Path] = [
    Path("/usr/share/kicad/symbols"),
    Path("/usr/local/share/kicad/symbols"),
    Path("/Applications/KiCad/KiCad.app/Contents/SharedSupport/symbols"),
]


# Module-level cache: symbol entryName -> raw S-expression text from system library
_RAW_LIB_SYMBOLS: dict[str, str] = {}


def _resolve_system_lib(lib_prefix: str) -> str | None:
    """Resolve a KiCad library prefix to its system .kicad_sym path.

    Checks KICAD_SYMBOL_DIR env var first, then the install tree belonging to
    the resolved kicad-cli, then standard install locations.
    Returns the full path string, or None if not found.
    """
    if not lib_prefix:
        return None
    filename = f"{lib_prefix}.kicad_sym"

    # Check env var override first
    env_dir = os.environ.get("KICAD_SYMBOL_DIR")
    if env_dir:
        candidate = Path(env_dir) / filename
        if candidate.exists():
            return str(candidate)

    # Then KiCad's own tree. A Unix prefix and a Windows install both use
    # share/kicad; the macOS .app bundle uses SharedSupport.
    root = _kicad_root()
    if root:
        for sub in ("share/kicad/symbols", "SharedSupport/symbols"):
            candidate = root / sub / filename
            if candidate.exists():
                return str(candidate)

    # Finally the standard locations, which still cover a symbols-only install
    # (Debian ships kicad-symbols separately from kicad).
    for d in _SYSTEM_SYM_DIRS:
        candidate = d / filename
        if candidate.exists():
            return str(candidate)

    return None


def _extract_raw_symbol(lib_path: str, symbol_name: str) -> str | None:
    """Extract raw S-expression text for a top-level symbol from a .kicad_sym file.

    Uses balanced-paren counting.  Skips sub-unit matches like ``PWR_FLAG_0_0``.
    """
    text = Path(lib_path).read_text()
    target = f'(symbol "{symbol_name}"'
    pos = 0
    while True:
        idx = text.find(target, pos)
        if idx == -1:
            return None
        after = idx + len(target)
        # Reject sub-unit names (e.g. PWR_FLAG_0_0)
        if after < len(text) and text[after] not in (" ", "\n", "\r"):
            pos = after
            continue
        depth = 0
        i = idx
        while i < len(text):
            if text[i] == "(":
                depth += 1
            elif text[i] == ")":
                depth -= 1
                if depth == 0:
                    return text[idx : i + 1]
            i += 1
        return None


def _replace_lib_symbol_block(text: str, sym_name: str, raw_text: str) -> str:
    """Replace a lib_symbol block inside ``(lib_symbols ...)`` with *raw_text*."""
    lib_sym_start = text.find("(lib_symbols")
    if lib_sym_start == -1:
        return text
    target = f'(symbol "{sym_name}"'
    pos = lib_sym_start
    while True:
        idx = text.find(target, pos)
        if idx == -1:
            return text
        after = idx + len(target)
        if after < len(text) and text[after] not in (" ", "\n", "\r"):
            pos = after
            continue
        # Count balanced parens to find end of block
        depth = 0
        i = idx
        while i < len(text):
            if text[i] == "(":
                depth += 1
            elif text[i] == ")":
                depth -= 1
                if depth == 0:
                    # Determine indent from line start
                    line_start = text.rfind("\n", 0, idx)
                    indent = text[line_start + 1 : idx] if line_start != -1 else ""
                    reindented = _reindent(raw_text, indent)
                    return text[:idx] + reindented + text[i + 1 :]
            i += 1
        return text


def _reindent(sexpr: str, indent: str) -> str:
    """Re-indent an S-expression block to use *indent* as the base."""
    lines = sexpr.split("\n")
    if not lines:
        return sexpr
    # Detect original base indent
    orig_indent = ""
    for ch in lines[0]:
        if ch in (" ", "\t"):
            orig_indent += ch
        else:
            break
    result = []
    for line in lines:
        stripped = line.lstrip()
        if not stripped:
            result.append("")
            continue
        orig_line_indent = ""
        for ch in line:
            if ch in (" ", "\t"):
                orig_line_indent += ch
            else:
                break
        if orig_line_indent.startswith(orig_indent):
            relative = orig_line_indent[len(orig_indent) :]
        else:
            relative = ""
        result.append(indent + relative + stripped)
    return "\n".join(result)


def _save_sch(sch) -> None:
    """Write schematic, then fix system library symbols that kiutils corrupts."""
    sch.to_file()
    if not _RAW_LIB_SYMBOLS:
        return
    path = sch.filePath
    text = Path(path).read_text()
    changed = False
    for sym_name, raw_text in _RAW_LIB_SYMBOLS.items():
        new_text = _replace_lib_symbol_block(text, sym_name, raw_text)
        if new_text != text:
            text = new_text
            changed = True
    if changed:
        Path(path).write_text(text)


def _fix_empty_tstamps(board: Board) -> None:
    """Fill in empty tstamp fields so kiutils can round-trip the file.

    KiCad 9 uses ``(uuid ...)`` instead of ``(tstamp ...)`` in segments,
    vias, footprints, graphic items, and zones.  kiutils 1.4.8 only
    handles ``tstamp``, so after loading a KiCad 9 board the tstamp
    attribute is left at its default (empty string or ``None``).  When
    kiutils writes the file it emits ``(tstamp )`` with no value, and
    the *next* load crashes with ``IndexError: list index out of range``
    because it tries ``item[1]`` on a single-element list.

    This helper assigns a fresh UUID to every object whose tstamp is
    empty or ``None`` so the saved file is always valid.
    """
    # Trace items: Segment and Via
    for item in board.traceItems:
        if not item.tstamp:
            item.tstamp = _gen_uuid()

    # Footprints
    for fp in board.footprints:
        if not fp.tstamp:
            fp.tstamp = _gen_uuid()

    # Graphic items (GrLine, GrText, GrArc, etc.)
    for gi in board.graphicItems:
        if hasattr(gi, "tstamp") and not gi.tstamp:
            gi.tstamp = _gen_uuid()

    # Zones
    for zone in board.zones:
        if not zone.tstamp:
            zone.tstamp = _gen_uuid()


_EMPTY_TSTAMP_RE = re.compile(r"\(tstamp\s*\)")


def _load_board(path: str = PCB_PATH) -> Board:
    """Load a KiCad PCB from *path*.

    Handles two KiCad 9 compatibility issues with kiutils 1.4.8:

    1. **Already-corrupted files**: A previous kiutils save may have
       written ``(tstamp )`` with no value.  kiutils crashes on
       ``item[1]`` when parsing these.  We fix the raw text before
       parsing.
    2. **uuid vs tstamp**: KiCad 9 uses ``(uuid ...)`` which kiutils
       ignores, leaving tstamp as ``""``.  After parsing we fill
       empties with fresh UUIDs so the *next* save is also valid.
    """
    if not path:
        raise ValueError("No PCB path provided. Pass pcb_path parameter.")

    # Pre-process: replace empty (tstamp ) with a generated UUID so
    # kiutils' parser doesn't crash on item[1].
    from kiutils.utils import sexpr

    raw = Path(path).read_text()
    _check_format_version(path, raw)
    fixed = _EMPTY_TSTAMP_RE.sub(lambda _m: f'(tstamp "{uuid.uuid4()}")', raw)
    board = Board.from_sexpr(sexpr.parse_sexp(fixed))
    board.filePath = path

    _fix_empty_tstamps(board)
    return board


# macOS keeps kicad-cli inside the .app bundle and never puts it on PATH.
_KICAD_APP = "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli"


@lru_cache(maxsize=1)
def _find_kicad_cli() -> str | None:
    """Absolute path to kicad-cli: KICAD_CLI_PATH, then PATH, then the macOS bundle.

    Resolved, not raw.  KiCad finds its stock symbol and footprint libraries at
    ``<exe_dir>/../SharedSupport``, so reaching it through a symlink on PATH
    makes DRC/ERC report bogus "library not found" violations while otherwise
    appearing to work.  ``shutil.which`` can also return a relative path: on
    Windows it searches the current directory before PATH.
    """
    found = os.environ.get("KICAD_CLI_PATH") or shutil.which("kicad-cli")
    if not found and os.path.isfile(_KICAD_APP):
        found = _KICAD_APP
    return str(Path(found).resolve()) if found else None


@lru_cache(maxsize=1)
def _kicad_root() -> Path | None:
    """The KiCad install root, derived from the resolved kicad-cli.

    KiCad locates its own data relative to the executable, so following the
    binary is correct on install layouts nobody has enumerated. Beats a list
    of hardcoded prefixes, which is what #6 was.
    """
    cli = _find_kicad_cli()
    return Path(cli).parent.parent if cli else None


def _run_cli(args: list[str], check: bool = True) -> subprocess.CompletedProcess:
    """Run a kicad-cli command, return CompletedProcess."""
    executable = _find_kicad_cli()
    if executable is None:
        raise RuntimeError("kicad-cli not found. Install KiCad, or set KICAD_CLI_PATH.")
    result = subprocess.run(
        [executable] + args,
        capture_output=True,
        text=True,
        errors="replace",
        timeout=120,
    )
    if check and result.returncode != 0:
        # kicad-cli can die with an empty stderr, e.g. the Windows access
        # violation when it cannot write to a OneDrive-redirected Documents.
        detail = result.stderr.strip() or f"no error output, exit code {result.returncode}"
        raise RuntimeError(f"kicad-cli failed: {detail}")
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


def _sym_ref_val_fp(sym) -> tuple[str, str, str]:
    """Extract (reference, value, footprint) from a SchematicSymbol's properties."""
    ref = next((p.value for p in sym.properties if p.key == "Reference"), "?")
    val = next((p.value for p in sym.properties if p.key == "Value"), "")
    fp = next((p.value for p in sym.properties if p.key == "Footprint"), "")
    return ref, val, fp


# symbol_instances entry, matching the shape the kiutils writer produced.
_SYM_INSTANCE_TPL = _cst.parse(
    b'(path "/x"\n\t\t\t(reference "R")\n\t\t\t(unit 1)\n\t\t\t(value "")'
    b'\n\t\t\t(footprint "")\n\t\t)'
).lists[0]


def _root_instance_target(schematic_path: str, project_path: str):
    """(tree, root, out_path, sym_path_prefix) for the root-instance file, or None.

    Sub-sheet: the root schematic with a 3-segment path prefix. Root itself
    (detected by a .kicad_pro sibling): the file with a 2-segment prefix.
    """
    root_path = _resolve_root(schematic_path, project_path)
    if root_path is None:
        root_path = _find_root_schematic(schematic_path)

    if root_path is not None:
        tree = _cst.parse(Path(root_path).read_bytes())
        root = tree.lists[0]
        target_name = Path(schematic_path).name
        sheet_uuid = None
        for sheet in root.find_all("sheet"):
            if _sheet_file_cst(sheet) == target_name:
                sheet_uuid = _node_uuid(sheet)
                break
        if sheet_uuid is None:
            return None
        return tree, root, root_path, f"/{_node_uuid(root)}/{sheet_uuid}"
    pro_path = Path(schematic_path).with_suffix(".kicad_pro")
    if not pro_path.exists():
        return None
    tree = _cst.parse(Path(schematic_path).read_bytes())
    root = tree.lists[0]
    return tree, root, schematic_path, f"/{_node_uuid(root)}"


def _upsert_root_symbol_instance(
    schematic_path: str,
    project_path: str,
    sym_uuid: str,
    reference: str,
    unit: int = 1,
    value: str = "",
    footprint: str = "",
) -> bool:
    """Create or update a symbol_instances entry in the root schematic.

    Automatically detects whether *schematic_path* is a sub-sheet or the root
    itself and builds the correct instance path accordingly.

    Returns True if the root was updated, False if no root could be determined.
    """
    target = _root_instance_target(schematic_path, project_path)
    if target is None:
        return False
    tree, root, out_path, prefix = target
    sym_path = f"{prefix}/{sym_uuid}"

    si = root.find("symbol_instances")
    if si is None:
        si = _cst.parse(b"(symbol_instances\n)").lists[0]
        tail = root.find("sheet_instances") or root.find("embedded_fonts")
        if tail is not None:
            root.insert_before(tail, si)
        else:
            root.children += [_cst.Node("ws", b"\n"), si]

    entry = next((e for e in si.find_all("path") if e.atoms[1].text == sym_path), None)
    if entry is None:
        entry = _SYM_INSTANCE_TPL.copy()
        entry.atoms[1].set_text(sym_path)
        entries = si.find_all("path")
        if entries:
            si.insert_after(entries[-1], entry)
        else:
            si.children += [_cst.Node("ws", b"\n\t\t"), entry]
    entry.find("reference").atoms[1].set_text(reference)
    entry.find("unit").atoms[1].set_text(str(unit))
    entry.find("value").atoms[1].set_text(value)
    entry.find("footprint").atoms[1].set_text(footprint)
    Path(out_path).write_bytes(_cst.serialize(tree))
    return True


def _remove_root_symbol_instance(
    schematic_path: str,
    project_path: str,
    sym_uuid: str,
) -> bool:
    """Remove a symbol_instances entry from the root schematic.

    Returns True if an entry was removed, False otherwise.
    """
    # Suffix matching needs no sheet lookup, only the root file itself
    # (the kiutils version likewise removed stale entries even when the
    # sheet block was gone from the root).
    root_path = _resolve_root(schematic_path, project_path)
    if root_path is None:
        root_path = _find_root_schematic(schematic_path)
    if root_path is not None:
        out_path = root_path
    else:
        if not Path(schematic_path).with_suffix(".kicad_pro").exists():
            return False
        out_path = schematic_path
    tree = _cst.parse(Path(out_path).read_bytes())
    root = tree.lists[0]

    si = root.find("symbol_instances")
    if si is None:
        return False
    suffix = f"/{sym_uuid}"
    matched = [e for e in si.find_all("path") if e.atoms[1].text.endswith(suffix)]
    if not matched:
        return False
    for e in matched:
        si.remove_child(e)
    if not si.find_all("path"):
        # kiutils omitted the empty section entirely; match that shape.
        root.remove_child(si)
    Path(out_path).write_bytes(_cst.serialize(tree))
    return True


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------


def _courtyard_bbox(fp: Footprint) -> dict | None:
    """Compute the bounding box of courtyard items on a footprint.

    Iterates ``fp.graphicItems``, collects coordinates from items on
    ``*.CrtYd`` layers, groups by layer, and returns the bounding box
    for the first layer found.

    Returns a dict with keys ``layer``, ``min_x``, ``min_y``, ``max_x``,
    ``max_y``, ``width``, ``height``, or ``None`` if no courtyard items.
    """
    # Collect points grouped by layer
    layer_points: dict[str, list[tuple[float, float]]] = {}

    for item in fp.graphicItems:
        layer: str | None = None
        pts: list[tuple[float, float]] = []

        if isinstance(item, FpLine):
            layer = item.layer
            pts = [
                (item.start.X, item.start.Y),
                (item.end.X, item.end.Y),
            ]
        elif isinstance(item, FpRect):
            layer = item.layer
            pts = [
                (item.start.X, item.start.Y),
                (item.end.X, item.end.Y),
            ]
        elif isinstance(item, FpCircle):
            layer = item.layer
            cx, cy = item.center.X, item.center.Y
            ex, ey = item.end.X, item.end.Y
            radius = math.sqrt((ex - cx) ** 2 + (ey - cy) ** 2)
            pts = [
                (cx - radius, cy - radius),
                (cx + radius, cy + radius),
            ]
        elif isinstance(item, FpArc):
            layer = item.layer
            pts = _linearize_arc(
                item.start.X,
                item.start.Y,
                item.mid.X,
                item.mid.Y,
                item.end.X,
                item.end.Y,
            )
        elif isinstance(item, FpPoly):
            layer = item.layer
            pts = [(c.X, c.Y) for c in item.coordinates]

        if layer is None or not layer.endswith(".CrtYd") or not pts:
            continue

        layer_points.setdefault(layer, []).extend(pts)

    if not layer_points:
        return None

    # Return the first layer found (prefer F.CrtYd, then B.CrtYd, then any)
    for preferred in ("F.CrtYd", "B.CrtYd"):
        if preferred in layer_points:
            chosen_layer = preferred
            break
    else:
        chosen_layer = next(iter(layer_points))

    points = layer_points[chosen_layer]
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)

    return {
        "layer": chosen_layer,
        "min_x": min_x,
        "min_y": min_y,
        "max_x": max_x,
        "max_y": max_y,
        "width": max_x - min_x,
        "height": max_y - min_y,
    }


def _point_in_polygon(x: float, y: float, polygon: list[tuple[float, float]]) -> bool:
    """Test if point (x, y) is inside *polygon* using ray-casting.

    Returns ``False`` for empty or degenerate polygons (< 3 points).
    """
    n = len(polygon)
    if n < 3:
        return False

    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            inside = not inside
        j = i

    return inside


def _transform_local_to_board(
    fp_x: float,
    fp_y: float,
    angle: float,
    local_x: float,
    local_y: float,
    mirrored: bool = False,
) -> tuple[float, float]:
    """Convert footprint-local coordinates to board coordinates.

    Applies rotation by *angle* (degrees) around the footprint origin
    ``(fp_x, fp_y)``.  When *mirrored* is True (back-side footprint),
    the local X coordinate is negated before rotation.
    """
    if mirrored:
        local_x = -local_x
    theta = math.radians(angle or 0)
    cos_t = math.cos(theta)
    sin_t = math.sin(theta)
    board_x = fp_x + (local_x * cos_t - local_y * sin_t)
    board_y = fp_y + (local_x * sin_t + local_y * cos_t)
    return board_x, board_y


def _board_edge_polygon(board: Board) -> list[tuple[float, float]] | None:
    """Extract the board outline from Edge.Cuts graphic items.

    Collects ``GrLine`` and ``GrArc`` segments on the ``Edge.Cuts`` layer,
    chains them into a closed polygon by endpoint matching (coordinates are
    rounded to 1 µm before matching),
    and returns the vertex list.  GrArc segments are linearized into ~16
    straight segments.

    Returns ``None`` if no Edge.Cuts lines exist or a closed polygon
    cannot be formed.
    """
    # Collect oriented segments as (start, end) tuples
    segments: list[tuple[tuple[float, float], tuple[float, float]]] = []

    for item in board.graphicItems:
        if isinstance(item, GrLine) and item.layer == "Edge.Cuts":
            s = (round(item.start.X, 3), round(item.start.Y, 3))
            e = (round(item.end.X, 3), round(item.end.Y, 3))
            if s != e:
                segments.append((s, e))
        elif isinstance(item, GrArc) and item.layer == "Edge.Cuts":
            # Linearize arc into ~16 line segments
            arc_pts = _linearize_arc(
                item.start.X,
                item.start.Y,
                item.mid.X,
                item.mid.Y,
                item.end.X,
                item.end.Y,
            )
            for k in range(len(arc_pts) - 1):
                s = (round(arc_pts[k][0], 3), round(arc_pts[k][1], 3))
                e = (round(arc_pts[k + 1][0], 3), round(arc_pts[k + 1][1], 3))
                if s != e:
                    segments.append((s, e))

    if not segments:
        return None

    # Build adjacency: endpoint -> list of (other_endpoint, segment_index)
    adjacency: dict[tuple[float, float], list[tuple[tuple[float, float], int]]] = {}
    for idx, (s, e) in enumerate(segments):
        adjacency.setdefault(s, []).append((e, idx))
        adjacency.setdefault(e, []).append((s, idx))

    # Chain into a closed polygon starting from the first segment
    used: set[int] = set()
    polygon: list[tuple[float, float]] = []

    start_pt = segments[0][0]
    current = start_pt
    polygon.append(current)

    while True:
        neighbors = adjacency.get(current, [])
        found = False
        for next_pt, seg_idx in neighbors:
            if seg_idx not in used:
                used.add(seg_idx)
                polygon.append(next_pt)
                current = next_pt
                found = True
                break
        if not found:
            break
        if current == start_pt:
            break

    # Verify closed polygon
    if len(polygon) < 4 or polygon[0] != polygon[-1]:
        return None

    # Remove closing duplicate
    return polygon[:-1]


def _linearize_arc(
    sx: float,
    sy: float,
    mx: float,
    my: float,
    ex: float,
    ey: float,
    num_segments: int = 16,
) -> list[tuple[float, float]]:
    """Approximate a 3-point arc (start, mid, end) as line segments.

    Returns a list of ``num_segments + 1`` points along the arc.
    """
    # Find circle center from three points
    ax, ay = sx, sy
    bx, by = mx, my
    cx, cy = ex, ey

    d = 2.0 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-10:
        # Degenerate (collinear) — just return start, mid, end
        return [(sx, sy), (mx, my), (ex, ey)]

    a_sq = ax * ax + ay * ay
    b_sq = bx * bx + by * by
    c_sq = cx * cx + cy * cy
    ux = (a_sq * (by - cy) + b_sq * (cy - ay) + c_sq * (ay - by)) / d
    uy = (a_sq * (cx - bx) + b_sq * (ax - cx) + c_sq * (bx - ax)) / d

    radius = math.sqrt((ax - ux) ** 2 + (ay - uy) ** 2)

    # Compute angles
    angle_start = math.atan2(sy - uy, sx - ux)
    angle_mid = math.atan2(my - uy, mx - ux)
    angle_end = math.atan2(ey - uy, ex - ux)

    # Determine sweep direction: start -> mid -> end
    def _normalize(a: float) -> float:
        return a % (2 * math.pi)

    # Check if going CCW (positive) or CW (negative) from start through mid to end
    d_start_mid = _normalize(angle_mid - angle_start)
    d_start_end = _normalize(angle_end - angle_start)

    if d_start_mid <= d_start_end:
        # CCW sweep
        sweep = d_start_end
    else:
        # CW sweep (negative direction)
        sweep = d_start_end - 2 * math.pi

    points: list[tuple[float, float]] = []
    for i in range(num_segments + 1):
        t = i / num_segments
        angle = angle_start + sweep * t
        px = ux + radius * math.cos(angle)
        py = uy + radius * math.sin(angle)
        points.append((px, py))

    return points


def _keepout_restrictions(ks: KeepoutSettings) -> dict[str, str]:
    """Return a dict of keepout restriction values from *ks*."""
    return {
        "tracks": ks.tracks,
        "vias": ks.vias,
        "pads": ks.pads,
        "copperpour": ks.copperpour,
        "footprints": ks.footprints,
    }


def _check_footprint_keepout_violations(board: Board, x: float, y: float, layer: str) -> list[dict]:
    """Check if position (x, y) violates any keepout zones on *layer*.

    Checks both board-level zones and footprint-embedded zones.

    Returns a list of violation dicts, each with keys:
    ``source`` (``"board"`` or ``"footprint:{ref}"``),
    ``layers``, and ``restrictions``.
    """
    violations: list[dict] = []

    # 1. Board-level keepout zones
    for zone in board.zones:
        ks = zone.keepoutSettings
        if ks is None:
            continue
        if ks.footprints != "not_allowed":
            continue
        # Check layer overlap
        if layer not in zone.layers:
            continue
        # Get polygon
        if not zone.polygons:
            continue
        poly_coords = [(round(c.X, 3), round(c.Y, 3)) for c in zone.polygons[0].coordinates]
        if _point_in_polygon(x, y, poly_coords):
            violations.append(
                {
                    "source": "board",
                    "layers": list(zone.layers),
                    "restrictions": _keepout_restrictions(ks),
                }
            )

    # 2. Footprint-embedded keepout zones
    for fp in board.footprints:
        fp_zones = getattr(fp, "zones", None)
        if not fp_zones:
            continue
        ref = _fp_ref(fp)
        if fp.position is None:
            continue
        fp_x = fp.position.X
        fp_y = fp.position.Y
        fp_angle = fp.position.angle or 0
        for zone in fp_zones:
            ks = zone.keepoutSettings
            if ks is None:
                continue
            if ks.footprints != "not_allowed":
                continue
            if layer not in zone.layers:
                continue
            if not zone.polygons:
                continue
            # Transform polygon from footprint-local to board coordinates
            poly_coords: list[tuple[float, float]] = []
            for c in zone.polygons[0].coordinates:
                bx, by = _transform_local_to_board(
                    fp_x, fp_y, fp_angle, c.X, c.Y, mirrored=fp.layer == "B.Cu"
                )
                poly_coords.append((round(bx, 3), round(by, 3)))
            if _point_in_polygon(x, y, poly_coords):
                violations.append(
                    {
                        "source": f"footprint:{ref}",
                        "layers": list(zone.layers),
                        "restrictions": _keepout_restrictions(ks),
                    }
                )

    return violations


def _promote_footprint_keepouts(pcb_path: str, output_path: str) -> int:
    """Promote footprint-level keepout zones to board-level in a temp copy.

    KiCad's ``ExportSpecctraDSN`` does not export keepout zones defined
    inside footprints.  This function copies the board, transforms
    footprint-local keepout zones into board-level zones, and saves
    the result to *output_path*.  The original PCB is never modified.

    Returns the number of keepout zones promoted.  If zero, *output_path*
    is not written.
    """
    import copy

    board = _load_board(pcb_path)
    count = 0

    for fp in board.footprints:
        fp_zones = getattr(fp, "zones", None)
        if not fp_zones:
            continue
        if fp.position is None:
            continue
        fp_x = fp.position.X
        fp_y = fp.position.Y
        fp_angle = fp.position.angle or 0
        mirrored = fp.layer == "B.Cu"

        for source_zone in fp_zones:
            ks = source_zone.keepoutSettings
            if ks is None:
                continue
            if not source_zone.polygons:
                continue

            for source_poly in source_zone.polygons:
                zone = Zone()
                zone.net = 0
                zone.netName = ""
                zone.layers = copy.deepcopy(source_zone.layers)
                zone.tstamp = _gen_uuid()
                zone.hatch = Hatch(style="edge", pitch=0.5)
                zone.keepoutSettings = copy.deepcopy(ks)

                transformed_poly = ZonePolygon()
                transformed_poly.coordinates = []
                for c in source_poly.coordinates:
                    bx, by = _transform_local_to_board(
                        fp_x, fp_y, fp_angle, c.X, c.Y, mirrored=mirrored
                    )
                    transformed_poly.coordinates.append(Position(X=bx, Y=by))

                zone.polygons = [transformed_poly]
                board.zones.append(zone)
                count += 1

    if count > 0:
        board.filePath = output_path
        try:
            board.to_file()
        except OSError as e:
            raise ToolError(f"Failed to prepare PCB for autorouting: {e}") from e

    return count
