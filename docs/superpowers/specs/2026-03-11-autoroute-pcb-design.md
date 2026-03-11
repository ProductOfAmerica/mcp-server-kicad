# Autoroute PCB Design Spec

**Date:** 2026-03-11
**Status:** Draft

## Summary

Add an `autoroute_pcb` MCP tool that automates PCB trace routing by integrating Freerouting (open-source Java autorouter) into the KiCad MCP server. The tool handles the full round-trip: export DSN from KiCad, run Freerouting, import routed SES back into a new KiCad PCB file.

## Decisions

- **Autorouter:** Freerouting (Java JAR, local CLI invocation)
- **DSN/SES conversion:** `pcbnew` Python module invoked via subprocess using KiCad's bundled Python interpreter (not imported directly, since `pcbnew` is a system dependency not available via pip)
- **Board I/O:** `kiutils` for all board reading/writing (consistent with existing codebase). `pcbnew` is only used for DSN/SES conversion via subprocess.
- **Tool shape:** Single `autoroute_pcb` tool with sensible defaults and optional configuration knobs
- **Output strategy:** Write to a new `_routed.kicad_pcb` file; never modify the original

## Tool API

```python
@mcp.tool(annotations=_EXPORT)
def autoroute_pcb(
    pcb_path: str = PCB_PATH,
    max_passes: int = 20,
    num_threads: int = 4,
    via_cost: int | None = None,
    timeout: int = 600,
    output_dir: str = OUTPUT_DIR,
) -> str:
```

### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `pcb_path` | `str` | `PCB_PATH` | Path to `.kicad_pcb` file |
| `max_passes` | `int` | `20` | Maximum autorouter optimization passes |
| `num_threads` | `int` | `4` | Thread count for routing |
| `via_cost` | `int \| None` | `None` | Via cost factor (higher = fewer vias) |
| `timeout` | `int` | `600` | Max seconds to wait for routing |
| `output_dir` | `str` | `OUTPUT_DIR` | Directory for output files (defaults to same dir as PCB) |

### Return Value

JSON object:

```json
{
  "routed_path": "/path/to/board_routed.kicad_pcb",
  "traces_added": 142,
  "vias_added": 23,
  "drc_violations": 0
}
```

Trace/via counts computed by comparing `board.traceItems` before and after using `kiutils` (consistent with existing codebase patterns).

## Freerouting JAR Management

New helper module: `mcp_server_kicad/_freerouting.py`

Contains all Freerouting-related logic:
- JAR discovery, download, and version management
- Java availability and version checking
- Subprocess invocation

### JAR Discovery Order

1. `FREEROUTING_JAR` environment variable (explicit path)
2. `~/.local/share/mcp-server-kicad/freerouting.jar` (auto-downloaded cache)

### Auto-Download

If no JAR is found, download a pinned release version from `freerouting/freerouting` GitHub releases to the cache directory. Verify SHA256 checksum after download. One-time operation (~20MB).

### Java Check

Verify `java` is on `PATH` and is version 17+ (Freerouting's minimum). If missing or too old, return clear error:
`"Java 17+ runtime required for autorouting. Install with: apt install default-jre"`

### Invocation

```bash
java -jar freerouting.jar -de board.dsn -do board.ses -mp {max_passes} -mt {num_threads}
```

If `via_cost` is provided, pass it as a Freerouting profile setting via the DSN file or Freerouting's `-dr` (design rules) flag. If Freerouting's CLI does not support a direct via cost flag, this parameter is deferred to a future version — document this in the tool's docstring.

Timeout is user-configurable (default 600s).

## DSN/SES Conversion via pcbnew Subprocess

Since `pcbnew` is a system-level C++ binding that ships with KiCad (not pip-installable), and the existing codebase uses `kiutils` for all file I/O, we invoke `pcbnew` via subprocess using KiCad's bundled Python interpreter:

```python
# Export DSN
subprocess.run([
    python_path, "-c",
    "import pcbnew; b = pcbnew.LoadBoard('{pcb}'); pcbnew.ExportSpecctraDSN(b, '{dsn}')"
], env={**os.environ, "PYTHONPATH": kicad_python_path}, ...)

# Import SES
subprocess.run([
    python_path, "-c",
    "import pcbnew; b = pcbnew.LoadBoard('{pcb}'); pcbnew.ImportSpecctraSES(b, '{ses}'); pcbnew.SaveBoard('{out}', b)"
], env={**os.environ, "PYTHONPATH": kicad_python_path}, ...)
```

### Locating KiCad's Python

`pcbnew` is a C++ binding that ships with KiCad, not available via pip. The helper must locate it:

1. **`KICAD_PYTHON` env var** — explicit path to Python interpreter with pcbnew available
2. **Probe common paths** — try `python3 -c "import pcbnew"` with `PYTHONPATH` set to known KiCad dist-packages locations:
   - Linux: `/usr/lib/kicad/lib/python3/dist-packages`, `/usr/lib/python3/dist-packages`
   - macOS: `/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/lib/python*/site-packages`
3. **Bare `python3`** — fallback attempt without `PYTHONPATH` (works if user has pcbnew on their default path)

Cache the result after first successful probe so subsequent calls are fast.

This keeps `pcbnew` as an external dependency (like `java`) rather than a Python import, avoiding crashes if `pcbnew` isn't available in the current Python environment.

## End-to-End Workflow

```
1. Count existing traces/vias via kiutils (_load_board) for before/after comparison
2. Export DSN via pcbnew subprocess → temp dir
3. Ensure Freerouting JAR exists (download if needed)
4. Run Freerouting subprocess → produces .ses file
5. Import SES via pcbnew subprocess → saves to {stem}_routed.kicad_pcb
6. Count new traces/vias via kiutils for comparison
7. (Optional) Run DRC via kicad-cli on the routed board
8. Return JSON summary
```

## File Management

- DSN and SES files created in a `tempfile.TemporaryDirectory` context manager (automatically cleaned up on success or failure)
- Output directory follows existing pattern: `out_dir = output_dir or str(Path(pcb_path).parent)`
- Routed PCB written to output dir with `_routed` suffix
- Original `.kicad_pcb` file is never modified

## Error Handling

| Condition | Behavior |
|-----------|----------|
| No Java on PATH or version < 17 | Return JSON error with install instructions |
| Freerouting JAR download fails | Return JSON error with manual download URL |
| pcbnew subprocess fails | Return JSON error: "KiCad Python bindings (pcbnew) not found. Ensure KiCad is installed." |
| Routing fails or times out | Return partial results if .ses was produced, error otherwise |
| No unrouted nets | Return early with message that board is already fully routed |

## Registration

- Tool added to `pcb.py` with `@mcp.tool(annotations=_EXPORT)` (reads input, writes new file — same pattern as `run_drc` and `export_gerbers`)
- Automatically aggregated by `server.py`'s `_copy_tools()` — no changes needed there
- Not gated behind `has_cli` since core flow uses `pcbnew` subprocess and Java, not `kicad-cli`
- DRC step is optional and only runs if `kicad-cli` is available
- `pcbnew` must NOT be imported at module level — only invoked via subprocess at runtime

## Testing

- **Happy path:** Mock both pcbnew subprocess and Freerouting subprocess. Provide a fixture SES file that adds traces. Verify the tool returns correct JSON and the output file exists.
- **Before/after counting:** Test trace/via count comparison using kiutils scratch boards
- **Error paths:** Missing Java, wrong Java version, missing JAR, failed pcbnew subprocess, failed routing
- **Test count update:** Update `test_unified_server.py` tool count assertions from 60→61 (with CLI) and 43→44 (without CLI), since `autoroute_pcb` is not gated behind `has_cli`
- **Annotation test:** Add `autoroute_pcb` to the `_EXPORT` parametrize list in `test_tool_annotations.py`

## Skill Update

Add `autoroute_pcb` to the `pcb-layout` skill's MCP Tools section so the LLM knows it's available.

## New Files

- `mcp_server_kicad/_freerouting.py` — JAR management, Java checks, and Freerouting subprocess invocation

## Modified Files

- `mcp_server_kicad/pcb.py` — Add `autoroute_pcb` tool
- `skills/pcb-layout/SKILL.md` — Add tool to MCP Tools reference
- `tests/test_pcb_write_tools.py` — Add autoroute tests
- `tests/test_unified_server.py` — Update tool count assertions
- `tests/test_tool_annotations.py` — Add to `_EXPORT` annotation list
