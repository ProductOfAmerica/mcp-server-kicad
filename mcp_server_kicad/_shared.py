"""Shared constants and helpers for KiCad MCP servers."""

import math
import os
import shutil
import subprocess
import uuid
from functools import lru_cache
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _dist_version
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

import mcp_server_kicad._cst as _cst

# ---------------------------------------------------------------------------
# Server construction
# ---------------------------------------------------------------------------

try:
    SERVER_VERSION = _dist_version("mcp-server-kicad")
except PackageNotFoundError:  # source checkout with no install
    SERVER_VERSION = "0.0.0+unknown"


def build_server(name: str, instructions: str) -> FastMCP:
    """Build a FastMCP server that reports *this package's* version.

    FastMCP takes no version argument and leaves the low-level server's
    version as None, in which case the SDK substitutes its own version. That
    is what clients and directory listings then display as the server
    version. Setting it through the private attribute is the only route the
    SDK offers; test_server_reports_package_version pins the behavior so an
    SDK upgrade that moves it fails loudly instead of silently regressing.
    """
    mcp = FastMCP(name, instructions=instructions)
    mcp._mcp_server.version = SERVER_VERSION
    return mcp


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
# Helpers
# ---------------------------------------------------------------------------


def _gen_uuid() -> str:
    return str(uuid.uuid4())


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


def _sheet_name_cst(sheet) -> str | None:
    """Sheet display name; KiCad 9 writes "Sheetname", kiutils "Sheet name"."""
    for key in ("Sheetname", "Sheet name"):
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

    # Sub-sheet: find its sheet block UUID in the root schematic (CST read,
    # so no format restriction on the root)
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
            root.append_child(si, b"\n")

    entry = next((e for e in si.find_all("path") if e.atoms[1].text == sym_path), None)
    if entry is None:
        entry = _SYM_INSTANCE_TPL.copy()
        entry.atoms[1].set_text(sym_path)
        entries = si.find_all("path")
        if entries:
            si.insert_after(entries[-1], entry)
        else:
            si.append_child(entry, b"\n\t\t")
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


def _xy(node) -> tuple[float, float]:
    """The two coordinates of a node like (start x y), (center x y) or (xy x y)."""
    return float(node.atoms[1].text), float(node.atoms[2].text)


# kiutils KeepoutSettings defaults, used when a (keepout) child is absent.
_KEEPOUT_DEFAULTS = {
    "tracks": "allowed",
    "vias": "allowed",
    "pads": "allowed",
    "copperpour": "not-allowed",
    "footprints": "not-allowed",
}


def _keepout_dict(ko) -> dict[str, str]:
    """Restriction values of a (keepout ...) node, defaults filling the gaps."""
    out = dict(_KEEPOUT_DEFAULTS)
    for k in out:
        child = ko.find(k)
        if child is not None:
            out[k] = child.atoms[1].text
    return out


def _courtyard_bbox_cst(fp) -> dict | None:
    """Courtyard bounding box of a CST footprint node, in footprint-local mm.

    Points are grouped by layer and the bbox comes from F.CrtYd, else
    B.CrtYd, else the first courtyard layer seen. Unrounded. A rect
    contributes only its two stored corners, which is what the retired
    kiutils twin did.

    Returns a dict with keys ``layer``, ``min_x``, ``min_y``, ``max_x``,
    ``max_y``, ``width``, ``height``, or ``None`` if no courtyard items.
    """
    layer_points: dict[str, list[tuple[float, float]]] = {}

    for item in fp.lists:
        pts: list[tuple[float, float]] = []
        if item.head in ("fp_line", "fp_rect"):
            pts = [_xy(item.find("start")), _xy(item.find("end"))]
        elif item.head == "fp_circle":
            cx, cy = _xy(item.find("center"))
            ex, ey = _xy(item.find("end"))
            radius = math.hypot(ex - cx, ey - cy)
            pts = [(cx - radius, cy - radius), (cx + radius, cy + radius)]
        elif item.head == "fp_arc":
            pts = _linearize_arc(
                *_xy(item.find("start")), *_xy(item.find("mid")), *_xy(item.find("end"))
            )
        elif item.head == "fp_poly":
            poly_pts = item.find("pts")
            pts = [_xy(p) for p in poly_pts.find_all("xy")] if poly_pts is not None else []
        else:
            continue

        layer = item.find("layer")
        if layer is None or not layer.atoms[1].text.endswith(".CrtYd") or not pts:
            continue
        layer_points.setdefault(layer.atoms[1].text, []).extend(pts)

    if not layer_points:
        return None

    preferred = ("F.CrtYd", "B.CrtYd")
    chosen_layer = next((p for p in preferred if p in layer_points), next(iter(layer_points)))

    xs = [p[0] for p in layer_points[chosen_layer]]
    ys = [p[1] for p in layer_points[chosen_layer]]
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


def _chain_edge_polygon(
    segments: list[tuple[tuple[float, float], tuple[float, float]]],
) -> list[tuple[float, float]] | None:
    """Chain (start, end) segments into a closed polygon by endpoint matching."""
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
