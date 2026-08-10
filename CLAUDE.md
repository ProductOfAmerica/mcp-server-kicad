# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
uv sync --frozen --all-extras --dev   # setup (CI uses exactly this)
uv run pytest -v                      # full suite
uv run pytest tests/test_cst.py -v    # one file
uv run pytest -k test_name            # one test
uv run ruff check .                   # lint
uv run ruff format --check .          # format check (CI enforces)
uv run pyright                        # type check (basic mode)
uv run python mcp_server_kicad/_cst.py  # CST self-check (demo() asserts)
```

Tests that shell out to `kicad-cli` (ERC, DRC, exports) auto-skip when it is not installed; so does the autouse fixture in `tests/conftest.py` that validates every generated `.kicad_sch` is parseable by `kicad-cli`. Confirm `kicad-cli` resolves locally (PATH, `KICAD_CLI_PATH`, or the macOS app bundle) or a green run proves less than it looks. Tests that intentionally write invalid files use `@pytest.mark.no_kicad_validation`.

Run a server by hand: `uv run mcp-server-kicad` (stdio), or via the MCP Inspector command in README.md.

## Architecture

Five FastMCP servers, one module each: `schematic.py`, `pcb.py`, `project.py`, `symbol.py`, `footprint.py`. Each defines its own `mcp` instance and `main()` console script; `server.py` merges all tools into the unified `kicad` server through `_copy_tools`, which reaches into FastMCP's private `_tool_manager._tools` (no public copy API; `test_tool_annotations.py` depends on the same internals). Every tool passes one of the annotation presets from `_shared.py` (`_READ_ONLY`, `_ADDITIVE`, `_DESTRUCTIVE`, `_EXPORT`) and returns a Pydantic model from `models.py` when it has structured output.

### Two parse/write stacks — know which one a tool is on

1. **CST substrate (`_cst.py`)**: stdlib-only, byte-preserving concrete syntax tree for s-expressions. Bytes in, bytes out; `serialize(parse(b)) == b` by construction; malformed input raises instead of being repaired. All `.kicad_sch` reads and writes (schematic.py and project.py), the eight board read tools, and `add_trace`/`add_via` run on it. These paths are **guard-free**: they work on KiCad 9 and KiCad 10 files.
2. **kiutils (legacy)**: the remaining board writers, `project.py` hierarchy reads, and the symbol/footprint library tools. These call `_check_format_version` first and **refuse KiCad 10 files** (read-max limits in `_FORMAT_VERSION_LIMITS`, `_shared.py`) because kiutils crashes on K10 boards and silently downgrades K10 schematics. Stock libraries under a KiCad install are exempt from the guard.

Two helper modules back the subprocess tools: `_freerouting.py` (`autoroute_pcb`; needs Java, resolves freerouting.jar via `FREEROUTING_JAR` or a cached auto-download, and hosts `find_pcbnew_python`) and `_netlist_import.py` (run under pcbnew's Python by `update_pcb_from_schematic` to load footprints and bind nets).

The governing invariant (non-negotiable): bytes the user did not ask to change reach the disk unchanged, and an edit that cannot be done correctly is refused with the file intact. `docs/adr-cst-substrate.md` is the decision record — read it before touching any write path. Its status log is the authoritative list of which tools sit on which stack; update it when a tool migrates. Migration is one mutation-kind per PR (strangler fig), each slice validated by a byte-preservation test, the ERC oracle, and the KiCad 10 CI gate.

Rules the ADR encodes that will bite if ignored:

- **CST I/O is bytes-only.** Never read a KiCad file in text mode on a write path; that rewrites CRLFs. Atom text decodes on demand; `set_text` re-encodes through the KiCad-measured escape codec (the only lossy surface).
- **Board net references are version-gated with zero dialect overlap**: emit numeric `(net N)` for board format ≤ 20241229, quoted `(net "NAME")` above it, and never copy a numeric net reference across versions — KiCad 10 silently rebinds numbers to the wrong net. KiCad 10 boards have no net table; nets are derived from usage.
- **Board CST cache** (`_open_pcb_cst`): parsed trees cached per resolved path keyed on mtime+size. Writers must pop their cache entry *before* mutating and never reinsert, so an exception mid-edit cannot leave a poisoned tree cached.
- New constructs the server emits come from templates harvested from KiCad's own output (or verbatim copies of system-library nodes), not hand-written s-expressions.

### Path and tool resolution (`_shared.py`)

Config priority: explicit tool parameter > `KICAD_SCH_PATH`/`KICAD_PCB_PATH`/`KICAD_SYM_LIB`/`KICAD_FP_LIB`/`KICAD_OUTPUT_DIR` env vars > auto-detect from a single `*.kicad_pro` in cwd (resolved at import time into module constants). `kicad-cli` resolves via `KICAD_CLI_PATH`, then PATH, then the macOS app bundle; everything else in the install (stock symbol libs, the pcbnew Python used by `fill_zones`/`autoroute_pcb`) derives from the resolved binary's location (`_kicad_root`). CLI-backed tools are always registered and fail with an actionable message when `kicad-cli` is missing.

### Tests

Fixtures in `conftest.py` build schematics/boards through kiutils builders. Byte-diff helpers (`_pure_insertion`, `_span_preserved`, `_confined`) assert CST edits touch only one contiguous span — use them in any new write-path test. `kicad_native_sch` mirrors KiCad's own output format where kiutils' differs; prefer it when testing format fidelity.

### CI

- `ci.yml`: lint + pyright + pytest matrix (3.10–3.13), no KiCad installed.
- `kicad-suite.yml`: full suite on Linux with KiCad 9. Runs on PRs.
- `macos-discovery.yml`: macOS with KiCad 10 — the gating KiCad 10 e2e tests live here. Runs only on pushes to `main` and `ci/**`, **not on PR branches**: validate KiCad-10-affecting changes pre-merge by pushing a `ci/**` branch.
- `release.yml`: manual dispatch. Bumps the version (pyproject, uv.lock, plugin.json), tags a GitHub release, publishes to PyPI.

Write formats target KiCad 9 (`KICAD_SCH_VERSION = 20250114` in conftest; write constants in `project.py`/`symbol.py` are distinct from the read-max guard limits). KiCad 10 capability exists only through CST paths.

### This repo is also a Claude Code plugin

`.claude-plugin/`, `skills/` (six design skills), `agents/` (reviewer subagents), and `hooks/` ship to plugin users. `hooks/hooks.json` blocks Read/Write/Edit on KiCad file extensions at the harness level — file manipulation must go through the MCP tools. Changes under `skills/` and `agents/` are user-facing product, not internal docs; `test_skills.py` and `test_skill_tool_coverage.py` check them against the real tool surface.
