# Handoff: routing prototypes and measurement harness (2026-10-04)

Paths are under `%TEMP%\pr57pt\` (%TEMP%\pr57pt). Reports live in `%TEMP%\pr57work\`: `hybrid-report.md` (design choice) and `dupref-report.md` (Hb2's duplicate-reference rule). The repo was never modified.

## Prototypes (coarse\, same API)
- **M2** (`m2_impl.py`): the first precise design, with certain and possible models; 24 measured wrong writes. **K** (`coarse_impl.py`): a coarse refusal model with attribution switches; it refuses too much.
- **H** (`h_impl.py`): K's margin and refusals, KiCad-rule names, no crossing joins, plus refusals for duplicate references and netclass. **Hb** (`hb_impl.py`) loads H with `CROSS_BAN = True`.
- **H2** (`h2_impl.py`): H with the pad-map duplicate-reference rule. **Hb2** (`hb2_impl.py`) loads H2 with `CROSS_BAN = True`.
- **Hb2 is the chosen design.** Its reference is `h2_impl.py` with `CROSS_BAN = True`; its docstring holds rules H-C1 to H-C19.
- **E** (`e_impl.py`): writes only into empty space. Safe, but it refuses most edits on existing designs.

## Environment
- **kicad-cli 9.0.8 on PATH.**
- **Run scripts with `%TEMP%/pr57pt/py script.py`** (Git Bash). That is the repo venv with `PYTHONPATH` pinned to `base54160c1`, a `git archive` of 54160c1. `harness.py` defaults `PT_REPO` to that export, and `coarse\judge3.py` raises if `mcp_server_kicad` resolves anywhere else. Never run from the repo directory: `''` on `sys.path` shadows the pin.
- **KiCad 9 demos** (`<KiCad 9.0 install>\share\kicad\demos`) are needed by the demo sweeps, probeB `s09_demo_sweep` and `n06b`, llm-workflow `lw_demo`, probeC perf (`pfix.py`), and the `dupref` oracle, isolate and strip tests. Layouts and stock fixtures are already copied.

## Judging one call (coarse\judge3.py; read its docstring)
```python
sys.path.insert(0, r"%TEMP%\pr57pt\coarse"); from judge3 import snapshot2, assess2, call
snap = snapshot2(root, edited, N="N")  # connect_pins: N=None
st, msg = call(mod.wire_pins_to_net, pins=[{"reference": "R1", "pin": "1"}], label_text="N", schematic_path=edited)
res = assess2(snap, root, [{("R1", "1")}], edited_path=edited, status=st)  # res["cli"]/["gui"]["wrong"], res["false_noop"]
```
- **Readings:** `cli` is the kicad-cli netlist (measured); `gui` is a port of the GUI load-cleanup, then kicad-cli (emulated). **Wrong** means any of: bad merge, split, rename, class change, pad in more nets, vanished or appeared node, `named_merge`, `req_pad_multi`.

## What an implementation must expose
- **Signatures** (module-level):
  - `wire_pins_to_net(pins, label_text, direction="auto", stub_length=2.54, auto_pwr_flag=True, schematic_path=...)`
  - `connect_pins(ref1, pin1, ref2, pin2, schematic_path=...)`
  - `auto_place_decoupling_cap(lib_id, reference, value, x, y, power_net, ground_net, rotation=0, symbol_lib_path="", schematic_path=..., project_path="")`
- **Outcomes:**
  - A refusal raises mcp `ToolError` and leaves every byte unchanged.
  - A no-op returns a string starting "No change", also with every byte unchanged.
  - Any other exception counts as ERROR.
- **Reason codes:** messages start `[H:code]`. The harness regexes accept only the prefixes M, K, H and E.
- **To test the repo's own tools at a new commit:**
  1. `git archive <sha> mcp_server_kicad tests` into `base<sha>`.
  2. Point `PT_REPO` and `PYTHONPATH` at it, and relax the guard at the end of `judge3.py`.
  3. Wrap the tools in an adapter module that maps them onto the outcomes above, then check fixture identity before trusting results: demo prepare must say "same bytes as follow-up 1 True", and `layouts_stock\check_bases.py` must report 67 equal.

## Full gate for a new module `x_impl.py` (put it in coarse\)
1. **Copy the harness.** Copy `coarse\rerun3` (including `meta\`) to `coarse\rerun4`, and replace "rerun3" with "rerun4" in every `.py`, `.json` and `.ps1`. `dupref\make_rerun3.py` is the template; it skips records.
2. **Swap the design.** Replace the single entry `{"Hb2": HB2}` with the new module in:
   - `probeA\rlib.py`, `probeB\rlib.py`, `llm-workflow\rlib.py`, `layouts_stock\runner.py`, `demo\common.py`;
   - `demo\run_sweeps.py` (`ORDER`);
   - `probeC\rlib.py` (`IMPLS`, and `MODS`, which used `HB2.MODULE`);
   - `probeC\composite\cmp_child.py` (inline map).

   `dupref\switch_hb2.py` shows each edit.
3. **Launch** with `pwsh -File %TEMP%\pr57pt\coarse\rerun4\meta\launch3.ps1`. It starts six detached, resumable batches; re-run it to resume.
   - Kill whole process trees: TaskStop leaves children running.
   - kicad-cli startup crashes (exit 3221225794) surface as errors; re-run those tasks.
   - Expect about **1 h 50 min** for one design on 16 logical CPUs (measured 10-03, 21:46 to 23:37).
4. **Gate:** `py %TEMP%\pr57pt\coarse\dupref\gate.py rerun4 X --json gate_x.json`, compared with `dupref\gate_rerun3_Hb2.json`. Pass means:
   - 0 wrong (cli and gui), and no ERROR or HARNESS_ERROR;
   - the only false no-op is DEC-23 (probeC `dec_d04` variant a), and the only unjudged call is probeB `h16 ... b_child_R32_3V3L:1`;
   - refusal rates at or below the Hb2 column in `dupref-report.md` section 5, or every rise explained.

   `gate.py rerun2 Hb` reproduces the hybrid report's Hb column, which checks the script itself.

## Probe cases worth porting into the repo's tests
Case IDs are indexed in `coarse\caseidx\*.json`; the builders are under `coarse\rerun3`.
- **PI-15** (true duplicate references must refuse): `probeA\pin-identity\pi_jobs.py`, variants `m_edges/13_R1` and `v10_dupref/C1`. **PI-17** (unresolved library symbol): `m_orphan/R1_1`.
- **INERT-05** (rule area with a netclass flag): `probeA\inert\inert_jobs.py`, variant `v05_rule_area/RA1`.
- **HIER-06** (sheet pin on a wire interior; +5V merges into GND): `probeB\scripts\hierarchy\h04_sheetpin_on_wire_interior.py`.
- **HIER-14 and UP-05** (unit-0 pad copied on another sheet): `h15_units_across_sheets.py` and `units-pads\h_units_two_sheets.py`.
- **DEC-14** (pad copies across sheets short): `probeC\decisions\dec_d11.py`. **DEC-23** (power-symbol pin no-op): `dec_d04.py`.
- **Crossing ban:** `coarse\h_build\crossban_probe.py` Q1 to Q5 (H's stub crosses net A; a repo label on the crossing merges /A and /N).
- **Duplicate-reference rule:** vme-wren's IC14 and IC20 must be accepted, and a 74LS00 plus a 74LS04 sharing U1 must refuse (`dupref\isolate_test.py`, `dupref\oracle.py`).

## Known limits
- **The GUI reading is emulated** (`verify-probe-perf\vcommon.gui_load_proxy`, a port of KiCad 9.0.8's load cleanup); eeschema was never driven. **There is no KiCad 10 reading**, so the jumper refusal and KiCad 10 files are unverified by any reader.
- **probeB H16** (a KiCad 10 `(power local)` symbol): kicad-cli 9 cannot load it, so it has no verdict for any design.
- **DEC-23 is a judge limit:** no netlist lists `#` nodes. `rerun2\meta\dec23_check.out` shows the no-op is correct.
- **judge3's blind spots:** label text that mimics an auto name (`Net-(...)`), and node sets listed twice, where it skips rename detection.
- **Quarantined records, never use:**
  - `coarse\rerun2\_superseded_pre_judgefix\` was judged before the `named_merge` fix;
  - `coarse\rerun2\_superseded_judge_crash\` holds a probeB shard log replaced after a kicad-cli crash.
- **rerun2 probeC** parts hold 3 duplicated calls per design in `gm_a2_facing`. Deduplicate by slot, as `gate.py` and the corrected report do.
