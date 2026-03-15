"""Pydantic response models for MCP tool structured output.

These models enable FastMCP's automatic ``outputSchema`` generation
and ``structuredContent`` population.  Every tool that returns
structured data should use one of these models as its return type.
"""

from __future__ import annotations

from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Schematic list-item models (replacing list_schematic_items)
# ---------------------------------------------------------------------------


class SchematicSummary(BaseModel):
    page_size: str
    page_width_mm: float
    page_height_mm: float
    components: int
    labels: int
    global_labels: int
    hierarchical_labels: int
    sheets: int
    wires: int
    junctions: int
    no_connects: int


class ComponentItem(BaseModel):
    reference: str
    value: str
    lib_id: str
    x: float
    y: float
    rotation: float


class LabelItem(BaseModel):
    text: str
    x: float
    y: float


class WireItem(BaseModel):
    x1: float
    y1: float
    x2: float
    y2: float


class GlobalLabelItem(BaseModel):
    text: str
    shape: str
    x: float
    y: float


class HierarchicalLabelItem(BaseModel):
    text: str
    shape: str
    x: float
    y: float
    rotation: float
    uuid: str


class SheetItem(BaseModel):
    sheet_name: str
    file_name: str
    x: float
    y: float
    width: float
    height: float
    pin_count: int
    uuid: str


class JunctionItem(BaseModel):
    x: float
    y: float
    diameter: float


class NoConnectItem(BaseModel):
    x: float
    y: float


class BusEntryItem(BaseModel):
    x: float
    y: float
    size_x: float
    size_y: float


# ---------------------------------------------------------------------------
# PCB list-item models (replacing list_pcb_items)
# ---------------------------------------------------------------------------


class PcbFootprintItem(BaseModel):
    reference: str
    value: str
    lib_id: str
    x: float
    y: float
    rotation: float
    layer: str


class TraceSegmentItem(BaseModel):
    type: str  # "segment" or "via"
    # segment fields
    start_x: float | None = None
    start_y: float | None = None
    end_x: float | None = None
    end_y: float | None = None
    width: float | None = None
    layer: str | None = None
    net: int | None = None
    # via fields
    x: float | None = None
    y: float | None = None
    size: float | None = None
    drill: float | None = None
    layers: list[str] | None = None


class NetItem(BaseModel):
    number: int
    name: str


class ZoneItem(BaseModel):
    net_name: str
    layers: list[str]
    priority: int
    is_keepout: bool
    keepout: dict | None = None
    polygon: list[dict] | None = None


class LayerItem(BaseModel):
    ordinal: int
    name: str
    type: str


class GraphicItem(BaseModel):
    type: str
    layer: str
    start_x: float | None = None
    start_y: float | None = None
    end_x: float | None = None
    end_y: float | None = None
    text: str | None = None
    x: float | None = None
    y: float | None = None


# ---------------------------------------------------------------------------
# DRC / ERC results
# ---------------------------------------------------------------------------


class DrcResult(BaseModel):
    source: str
    kicad_version: str
    violation_count: int
    violations: list[dict]
    unconnected_count: int
    unconnected_items: list[dict]


class ErcResult(BaseModel):
    source: str
    kicad_version: str
    violation_count: int
    violations: list[dict]
    note: str | None = None


# ---------------------------------------------------------------------------
# Export results (inheritance for DRY)
# ---------------------------------------------------------------------------


class ExportResult(BaseModel):
    path: str
    size_bytes: int
    format: str


class PcbExportResult(ExportResult):
    layers: list[str]


class SingleGerberExportResult(ExportResult):
    layer: str


class MultiFileExportResult(BaseModel):
    path: str
    format: str
    files: list[str]
    count: int


class GerberExportResult(MultiFileExportResult):
    drill_files: list[str] = []
    drill_count: int = 0


class RenderExportResult(ExportResult):
    width: int
    height: int
    side: str


class BomExportResult(ExportResult):
    component_count: int


class PositionExportResult(ExportResult):
    component_count: int


# ---------------------------------------------------------------------------
# PCB operation results
# ---------------------------------------------------------------------------


class ZoneResult(BaseModel):
    net: str
    layer: str
    corners: int
    clearance_mm: float


class KeepoutZoneResult(BaseModel):
    corners: int
    layers: list[str]
    restrictions: dict


class FillZonesResult(BaseModel):
    zones_filled: int
    status: str


class TraceWidthResult(BaseModel):
    traces_modified: int
    net: str | None
    new_width_mm: float


class RemoveTracesResult(BaseModel):
    traces_removed: int
    net: str | None
    layer: str | None


class ThermalViasResult(BaseModel):
    vias_added: int
    reference: str
    pad: str
    net: str
    center: dict


class NetClassResult(BaseModel):
    net_class: str
    nets_assigned: int
    track_width_mm: float | None
    clearance_mm: float | None


class DanglingTracksResult(BaseModel):
    tracks_removed: int
    iterations: int


class FootprintBoundsResult(BaseModel):
    reference: str
    position: dict
    rotation: float
    courtyard: dict | None
    layer: str


class BoardValidationResult(BaseModel):
    total_footprints: int
    violations: list[dict]
    board_edge_checked: bool
    status: str


class PlacementCheckResult(BaseModel):
    status: str
    board_edge_checked: bool
    keepout_violations: list
    outside_board_edge: bool


class AutorouteResult(BaseModel):
    routed_path: str
    traces_added: int
    vias_added: int
    text_fields_fixed: int
    drc_violations: int | None = None
    drc_unconnected: int | None = None


# ---------------------------------------------------------------------------
# Schematic operation results
# ---------------------------------------------------------------------------


class NetConnectionsResult(BaseModel):
    net: str
    label_count: int
    connections: list[dict]


class UnconnectedPinsResult(BaseModel):
    unconnected_count: int
    pins: list
    note: str | None = None


# ---------------------------------------------------------------------------
# Project / hierarchy results
# ---------------------------------------------------------------------------


class HierarchyValidationResult(BaseModel):
    status: str
    issue_count: int
    issues: list[dict]


class RootSchematicResult(BaseModel):
    is_root: bool
    root_path: str | None


class HierarchyResult(BaseModel):
    root: str
    component_count: int
    sheet_count: int
    sheets: list[dict]


class SheetInfoResult(BaseModel):
    sheet_name: str
    file_name: str
    uuid: str
    x: float
    y: float
    width: float
    height: float
    pins: list[dict]
    component_count: int | None = None
    label_count: int | None = None
    hierarchical_label_count: int | None = None


class NetTraceResult(BaseModel):
    net_name: str
    sheets_touched: list[str]
    connection_count: int
    connections: list[dict]


class CrossSheetNetsResult(BaseModel):
    hierarchical_nets: list[dict]
    global_nets: list[dict]


class SymbolInstancesResult(BaseModel):
    instances: list[dict]
    count: int


class HierarchicalNetlistResult(BaseModel):
    output_path: str
    component_count: int
    net_count: int
    components: list[dict]
    nets: list[dict]


class VersionResult(BaseModel):
    version_info: str
