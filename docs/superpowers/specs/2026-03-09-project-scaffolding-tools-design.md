# KiCad Project Scaffolding Tools

## Problem

The MCP server has tools to manipulate existing schematics, PCBs, and symbol libraries, but no tools to create them from scratch. This forces Claude to write raw KiCad s-expression files using the Write tool, which is error-prone because the format is complex and Claude's training data may be stale.

## Solution

Add 5 project scaffolding tools to the schematic server. Code lives in a separate module (`project.py`) but tools register on the existing schematic FastMCP instance via `register_tools(mcp)`.

## Tools

### 1. `create_project(directory: str, name: str) -> str`

Creates a KiCad 9 project in the given directory.

- Creates `{directory}/{name}.kicad_pro` with minimal valid JSON boilerplate
- Creates `{directory}/{name}.kicad_prl` with empty local settings JSON
- Creates the directory if it doesn't exist
- Errors if `.kicad_pro` already exists (no silent overwrite)
- Returns: `"Created project at {directory}/{name}.kicad_pro"`

### 2. `create_schematic(schematic_path: str) -> str`

Creates a valid empty KiCad 9 schematic file.

- Writes a `.kicad_sch` with version header, generator, UUID, and empty content
- Errors if file already exists
- Returns: `"Created schematic at {path}"`

### 3. `create_symbol_library(symbol_lib_path: str) -> str`

Creates a valid empty KiCad 9 symbol library.

- Writes a `.kicad_sym` with version header and no symbols
- Errors if file already exists
- Returns: `"Created symbol library at {path}"`

### 4. `create_sym_lib_table(directory: str, entries: list[dict]) -> str`

Creates a `sym-lib-table` file in the given directory.

- Each entry dict has `name` (str) and `uri` (str) keys
- Generates `(lib (name "X") (type "KiCad") (uri "Y") (options "") (descr ""))` for each
- Overwrites if file exists (these files are small and declarative)
- Returns: `"Created sym-lib-table with {n} entries"`

### 5. `add_hierarchical_sheet(parent_schematic_path: str, sheet_name: str, sheet_file: str, pins: list[dict], x: float = 25.4, y: float = 25.4) -> str`

Adds a hierarchical sheet reference to a parent schematic and creates matching labels in the sub-schematic.

- `pins` is a list of dicts with `name` (str) and `direction` (str: input/output/bidirectional/tri_state/passive)
- Adds a `(sheet ...)` block to the parent with `(pin ...)` entries
- Opens the sub-schematic and adds matching `(hierarchical_label ...)` entries
- Sheet box auto-sized based on pin count
- Hierarchical labels auto-positioned vertically on the left edge of the sub-sheet
- All coordinates snapped to 1.27mm grid
- Sub-schematic file must already exist (call `create_schematic` first)
- Returns: `"Added sheet '{sheet_name}' with {n} pins to {parent_path}"`

## Integration

In `schematic.py`, before `main()`:

```python
from .project import register_tools as _register_project_tools
_register_project_tools(mcp)
```

`project.py` exports:

```python
def register_tools(mcp: FastMCP) -> None:
    # Registers all 5 tools on the given FastMCP instance
```

## Not included (YAGNI)

- No `create_pcb` — different workflow
- No `update_sym_lib_table` — just overwrite; small files
- No template/preset system
- No `create_footprint_library`

## File format references

### .kicad_pro (minimal)

```json
{
  "meta": {
    "filename": "{name}.kicad_pro",
    "version": 1
  }
}
```

### .kicad_prl (minimal)

```json
{
  "meta": {
    "filename": "{name}.kicad_prl",
    "version": 3
  }
}
```

### .kicad_sch (empty, via kiutils)

Use `Schematic()` with version/generator set, then `.to_file()`.

### .kicad_sym (empty, via kiutils)

Use `SymbolLib()` with version set, then `.to_file()`.

### sym-lib-table

```
(sym_lib_table
  (version 7)
  (lib (name "X")(type "KiCad")(uri "Y")(options "")(descr ""))
)
```
