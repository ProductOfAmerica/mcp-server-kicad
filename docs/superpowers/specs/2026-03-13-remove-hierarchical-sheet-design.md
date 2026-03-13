# Remove Hierarchical Sheet Tool

## Summary

Add a `remove_hierarchical_sheet` MCP tool to `project.py` that removes a hierarchical sheet block from a parent schematic. This fills a gap where `add_hierarchical_sheet` exists but there is no way to remove one — forcing users to manually edit schematics in KiCad when a sheet block needs to be deleted.

## Motivation

When a hierarchical sheet block is created in error (e.g., a duplicate), there is no MCP tool to remove it. `remove_component` only handles `schematicSymbols`, not `HierarchicalSheet` objects. Users must open KiCad and manually delete the block, breaking the all-MCP workflow.

## Tool Signature

```python
@mcp.tool(annotations=_DESTRUCTIVE)
def remove_hierarchical_sheet(
    parent_schematic_path: str,
    name: str | None = None,
    uuid: str | None = None,
    delete_child_file: bool = False,
) -> str:
```

### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `name` | `str \| None` | `None` | Sheet name to match via `sheet.sheetName.value` |
| `uuid` | `str \| None` | `None` | Sheet UUID for unambiguous identification |
| `delete_child_file` | `bool` | `False` | If True, also delete the child .kicad_sch file |
| `parent_schematic_path` | `str` | required | Path to the parent schematic (no default; matches `add_hierarchical_sheet` convention) |

At least one of `name` or `uuid` must be provided.

## Location

`mcp_server_kicad/project.py`, adjacent to `add_hierarchical_sheet`.

### Import Changes

- Add `_DESTRUCTIVE` to the imports from `_shared.py` (currently only `_ADDITIVE`, `_EXPORT`, `_READ_ONLY` are imported).
- Use `pathlib.Path` (already imported) for child file deletion via `Path.unlink()`.

### Code Pattern

Follow the three-layer pattern used by all `project.py` tools:
1. Private function: `_remove_hierarchical_sheet(...)`
2. Public alias: `remove_hierarchical_sheet = _remove_hierarchical_sheet`
3. MCP wrapper: `@mcp.tool(annotations=_DESTRUCTIVE)` calling the private function

### Save Method

Use `sch.to_file()` directly, consistent with all other `project.py` tools. `_save_sch()` from `schematic.py` is not needed here because this operation does not modify `libSymbols` (the corruption that `_save_sch` guards against).

### FastMCP Instructions

Update the `mcp` FastMCP instructions string in `project.py` to mention `remove_hierarchical_sheet` as the inverse of `add_hierarchical_sheet`.

## Behavior

### Identification Logic

1. If neither `name` nor `uuid` provided: return error immediately.
2. If `uuid` provided: match sheet by `sheet.uuid`. Normalize UUIDs before comparing (strip hyphens, lowercase) to handle format differences between KiCad versions and `_gen_uuid()`.
3. If only `name` provided: match by `sheet.sheetName.value` (the dedicated kiutils attribute, not a property key lookup — this avoids the "Sheet name" vs "Sheetname" key inconsistency).
4. If both provided: filter by UUID first, then verify the name matches. If UUID matches but name doesn't, return an error noting the mismatch.
5. Zero matches: return error.
6. Multiple matches (name-only): return error with disambiguation info (UUIDs and positions via `sheet.position.X`, `sheet.position.Y`).

### Child File Deletion

When `delete_child_file=True`:

1. Resolve child file path relative to the parent schematic's directory using `sheet.fileName.value` (the dedicated kiutils attribute).
2. Check if any *other* sheet in `sch.sheets` references the same child file (compare `fileName.value`).
3. If still referenced: skip file deletion, include warning in response.
4. If not referenced: delete the file with `Path.unlink()`.

### Removal

1. Remove the `HierarchicalSheet` object from `sch.sheets`.
2. Save via `sch.to_file()`.

Note: connected wires, labels, and other objects are NOT removed. The caller handles cleanup using existing tools (`remove_wire`, `remove_label`, etc.).

## Response Messages

- **Success:** `"Removed hierarchical sheet '{name}' (uuid={uuid})."`
- **Success + child deleted:** `"Removed hierarchical sheet '{name}' (uuid={uuid}). Deleted child file '{file}'."`
- **Success + child kept:** `"Removed hierarchical sheet '{name}' (uuid={uuid}). Kept child file '{file}' — still referenced by another sheet block."`
- **No match:** `"No hierarchical sheet found matching {criteria}."`
- **Ambiguous:** `"Multiple sheets named '{name}' found: [uuid=X at (x,y), uuid=Y at (x,y)]. Provide uuid to disambiguate."`
- **No parameters:** `"Provide at least one of 'name' or 'uuid'."`
- **Name/UUID mismatch:** `"Sheet with uuid={uuid} found but its name is '{actual}', not '{expected}'."`

## Testing

Tests go in `tests/test_project_tools.py` alongside existing `add_hierarchical_sheet` tests:

1. **Remove by UUID:** Create a sheet, remove by UUID, verify `sch.sheets` no longer contains it
2. **Remove by name (unique):** Create a sheet with a unique name, remove by name
3. **Ambiguous name error:** Create two sheets with the same name, attempt removal by name only — verify error with disambiguation info including UUIDs and positions
4. **Remove by name + uuid:** Create two same-named sheets, remove one by providing both name and UUID
5. **Name/UUID mismatch error:** Provide a valid UUID but wrong name — verify error
6. **delete_child_file=True, no other references:** Verify child file is deleted
7. **delete_child_file=True, still referenced:** Create two sheet blocks pointing to the same child file, remove one — verify child file is kept and warning is returned
8. **No match:** Attempt to remove a non-existent sheet — verify error message
9. **No parameters:** Call with neither name nor uuid — verify error
