# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
uv sync --frozen --all-extras --dev   # setup (CI uses exactly this)
uv run pytest -v -n auto              # full suite (CI uses -n auto everywhere)
uv run pytest tests/test_cst.py -v    # one file (no -n: worker startup dwarfs the run)
uv run pytest -k test_name            # one test
uv run ruff check .                   # lint
uv run ruff format --check .          # format check (CI enforces)
uv run pyright                        # type check (basic mode)
uv run python mcp_server_kicad/_cst.py  # CST self-check (demo() asserts)
```

Tests that shell out to `kicad-cli` (ERC, DRC, exports) auto-skip when it is not installed; so does the autouse fixture in `tests/conftest.py` that validates every generated `.kicad_sch` is parseable by `kicad-cli`. Confirm `kicad-cli` resolves locally (`KICAD_CLI_PATH`, PATH, the macOS app bundle, or the versioned Windows install roots) or a green run proves less than it looks. Tests that intentionally write invalid files use `@pytest.mark.no_kicad_validation`.

That validation fixture is what makes the suite slow: it spawns a `kicad-cli` process per generated schematic, ~380 ms each. It is memoised on the file's UUID-normalised digest, so each distinct schematic body is validated once per process rather than once per test (376 spawns -> 186, measured 2026-08-10); `test_validation_memo.py` guards the key against collapsing anything but a UUID. With `-n auto` on top, a full local run went 213 s -> 40 s on 16 cores.

Run a server by hand: `uv run mcp-server-kicad` (stdio), or via the MCP Inspector command in README.md.

## Architecture

Five `MCPServer` servers, one module each: `schematic.py`, `pcb.py`, `project.py`, `symbol.py`, `footprint.py`. Each defines its own `mcp` instance and `main()` console script; `server.py` merges all tools into the unified `kicad` server through `_copy_tools`, which reaches into the private `_tool_manager._tools` (no public copy API; `test_tool_annotations.py` depends on the same internals). Every tool passes one of the annotation presets from `_shared.py` (`_READ_ONLY`, `_ADDITIVE`, `_DESTRUCTIVE`, `_EXPORT`) and returns a Pydantic model from `models.py` when it has structured output.

### One parse/write stack: the CST substrate (`_cst.py`)

Stdlib-only, byte-preserving concrete syntax tree for s-expressions. Bytes in, bytes out; `serialize(parse(b)) == b` by construction; malformed input raises instead of being repaired. As of slice 18 every tool runs on it: all `.kicad_sch` reads and writes, all board reads and writes including the `autoroute_pcb` internals, and the symbol/footprint library tools. There is no version guard left, so KiCad 9 and KiCad 10 files both work everywhere. kiutils is a **test-only** dependency (the `dev` extra): it builds fixtures and reads written files back as an independent oracle, and nothing under `mcp_server_kicad` imports it (`test_runtime_imports_no_kiutils` scans for that).

Two helper modules back the subprocess tools: `_freerouting.py` (`autoroute_pcb`; needs Java, resolves freerouting.jar via `FREEROUTING_JAR` or a cached auto-download, hosts `find_pcbnew_python`, and era-preflights pcbnew: a KiCad 10 board on a pcbnew 9 refuses up front, a KiCad 9 board through pcbnew 10 gets a result warning that the routed copy is KiCad 10 format) and `_netlist_import.py` (run under pcbnew's Python by `update_pcb_from_schematic` to load footprints and bind nets).

The governing invariant (non-negotiable): bytes the user did not ask to change reach the disk unchanged, and an edit that cannot be done correctly is refused with the file intact. `docs/adr-cst-substrate.md` is the decision record; read it before touching any write path. Its status log records how each surface reached the substrate, slice by slice, and ends with the migration complete; append to it when a write path changes shape. The bar every slice cleared, and any new write path still has to clear, is a byte-preservation test, the ERC oracle, and the KiCad 10 CI gate.

Rules the ADR encodes that will bite if ignored:

- **CST I/O is bytes-only.** Never read a KiCad file in text mode on a write path; that rewrites CRLFs. Atom text decodes on demand; `set_text` re-encodes through the KiCad-measured escape codec (the only lossy surface).
- **Board net references are version-gated with zero dialect overlap**: emit numeric `(net N)` at or below `_NUMERIC_NET_VERSION_MAX` (20241229, defined in `pcb.py`), quoted `(net "NAME")` above it, and never copy a numeric net reference across versions. KiCad 10 silently rebinds numbers to the wrong net; its boards have no net table, so nets are derived from usage.
- **Board CST cache** (`_open_pcb_cst`): parsed trees cached per resolved path keyed on mtime+size. Writers must pop their cache entry *before* mutating and never reinsert, so an exception mid-edit cannot leave a poisoned tree cached.
- New constructs the server emits come from templates harvested from KiCad's own output (or verbatim copies of system-library nodes), not hand-written s-expressions.

### Path and tool resolution (`_shared.py`)

Config priority: explicit tool parameter > `KICAD_SCH_PATH`/`KICAD_PCB_PATH`/`KICAD_SYM_LIB`/`KICAD_FP_LIB`/`KICAD_OUTPUT_DIR` env vars > auto-detect from a single `*.kicad_pro` in cwd (resolved at import time into module constants). `kicad-cli` resolves via `KICAD_CLI_PATH`, then PATH, then the macOS app bundle, then the default Windows install roots (`_KICAD_WIN_DIRS`: Program Files and per-user LOCALAPPDATA, highest version number first); everything else in the install (stock symbol libs, the pcbnew Python used by `fill_zones`/`autoroute_pcb`) derives from the resolved binary's location (`_kicad_root`). CLI-backed tools are always registered and fail with an actionable message when `kicad-cli` is missing. `_run_cli` also repairs one specific crash: exit `_KICAD_STARTUP_CRASH` (3221225477) means KiCad could not create its data folder, so it retries once with `KICAD_DOCUMENTS_HOME` set to a local path and caches that in `_documents_home`. Gate on the exit code, never the stderr text, which is locale-dependent; and that crash raises regardless of `check`, because `check=False` exists for ERC and DRC violation counts, not for a process that died before running.

### Tests

Fixtures in `conftest.py` build schematics/boards through kiutils builders. Byte-diff helpers (`_pure_insertion`, `_span_preserved`, `_confined`) assert CST edits touch only one contiguous span; use them in any new write-path test. `kicad_native_sch` mirrors KiCad's own output format where kiutils' differs; prefer it when testing format fidelity.

### CI

- `ci.yml`: lint + pyright + pytest matrix (3.10-3.13), no KiCad installed. Triggers on `ci/**` pushes too, so preverify branches get the full matrix.
- `kicad-suite.yml`: full suite on Linux with KiCad 9. Runs on PRs and `ci/**` pushes.
- `macos-discovery.yml`: macOS with KiCad 10; the gating KiCad 10 e2e tests live here. Runs only on pushes to `main` and `ci/**`, **not on PR branches**: validate KiCad-10-affecting changes pre-merge by pushing a `ci/**` branch.
- `release.yml`: manual dispatch, and the whole release. Bumps the version (pyproject, uv.lock, plugin.json, server.json), tags a GitHub release, publishes to PyPI, then publishes to the MCP Registry with `mcp-publisher login github-oidc` (never the interactive device flow: that JWT expires five minutes after issue). Its bump commit is pushed with the default GITHUB_TOKEN, so it triggers no CI runs; zero runs on a bump commit is normal. Registry ownership is proved by the `mcp-name:` token in the README as published on PyPI, and registry metadata is immutable per version, so description fixes need a new release. The registry step runs last and retries, because the registry rejects a package version PyPI is not serving yet.

New files are stamped with the KiCad 9 formats the templates were harvested at (`KICAD_SCH_VERSION = 20250114` in conftest; `_EMPTY_SCH_TPL` in `project.py`, `_SYM_LIB_TPL` in `symbol.py`). Editing an existing file never changes its version stamp, so a KiCad 10 file stays KiCad 10.

### This repo is also a Claude Code plugin

`.claude-plugin/`, `skills/` (six design skills), `agents/` (reviewer subagents), and `hooks/` ship to plugin users. `hooks/hooks.json` blocks Read/Write/Edit on KiCad file extensions at the harness level: file manipulation must go through the MCP tools. Changes under `skills/` and `agents/` are user-facing product, not internal docs; `test_skills.py` and `test_skill_tool_coverage.py` check them against the real tool surface.
