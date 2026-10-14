# Routing harness

The measurement harness behind `docs/adr-routing-safety.md`. It runs one implementation of the three
routing tools (`wire_pins_to_net`, `connect_pins`, `auto_place_decoupling_cap`) over a few thousand
schematic inputs built from KiCad's demo projects and symbol libraries, and judges every call against
KiCad's netlist of the file before and after it (`core/judge3.py`). A write is wrong when the netlist after the call
merges a net it should not, splits one, renames one, changes a net class, loses or gains a node, or
leaves a requested pad in two nets. A refusal must leave every byte unchanged, and a no-op must leave
the request already met (a no-op that is not is a false no-op).

What the ADR used it for:

- choosing the design: M2, E and Hb2 (and H, Hb) ran on identical inputs, which is where ADR-R1's
  table of wrong writes and refusal rates comes from (`reports/hybrid-report.md` sections 3 and 4,
  `reports/dupref-report.md` section 5);
- the crossing ban of ADR-R2 (`analysis/crossban/crossban_probe.py`), the duplicate-reference rule
  of ADR-R3 (`analysis/dupref/`) and the `add_label` figure of ADR-R4 (`analysis/followup2/`);
- the slice 1 gate in the ADR's status log: the repository's own `wire_pins_to_net`, run as design X
  on the same 4,816 inputs (`baselines/gate_x_final2.json`), then rerun on commit 8d10aa4 over the
  inputs the later fixes could reach (`baselines/gate_x_final3.json`).

This directory holds code and small data only. No results, logs, netlists or copies of KiCad's demo
files are committed: demo inputs are copied from the installed KiCad at prepare time into a
git-ignored work directory (`work/`).

## Provenance

The pressure test ran from 2026-10-01 to 2026-10-04 in two scratch directories (`pr57pt`, the
harness; `pr57work`, the reports), which are not durable. This is that harness, ported so that it
runs from a clean checkout against the repository's own tools. The port changes paths, outputs and
the few API differences listed below; every script keeps its original logic, naming and layout (no
reformatting, no lint fixes), which is why `tools/routing_harness` is excluded from ruff, pyright,
pytest collection and the sdist (`pyproject.toml`). `reports/` holds the six reports the ADR cites;
`reports/HANDOFF.md` is the scratch-era handoff, kept as a historical record (its commands are
replaced by `run.py`, `prepare.py` and `core/gate.py`), and one line of `pressure-test-report.md`,
which described a local tool setup, is replaced by a neutral one. `baselines/derive.py` records how
the data files under `baselines/`, `caseidx/` and `reports/` were derived, scrubbed or copied from the
scratch directories, and its `--check` re-derives them while those still exist. Seven suite data
files are outside it: five copies of scratch `coarse/rerun3` files, identical apart from line endings
(`suites/layouts_stock/pinsets.json`, `suites/probeA/inert/netclass_template.kicad_pro`,
`suites/probeA/pin-identity/demo_calls.json`, `suites/probeC/jobs.txt`, `suites/probeC/perf/pins.json`),
and two job lists adapted from that run's `meta/` (working directories made relative to the harness,
outputs moved into the run directory: `suites/probeA/jobs_probeA.json`,
`suites/llm-workflow/jobs_llm.json`).

Tags used below: **measured** (a run printed it; the date and where are given), **inference** (reasoning,
not run).

## Layout

```
hpaths.py       every path and the bootstrap every script starts with
run.py          launch the six batches of a run (detached, resumable)
prepare.py      build a run's fixtures and check them against the baselines'
smoke.py        the smoke subset (eleven steps)
core/           harness.py (raw writers, netlist helpers), judge3.py (the judge), gate.py,
                seqrun.py, watch_bad.py, selftest_named_merge.py, registry.py (design names),
                readings.py, gui_reading.py (stub), compat54.py (frozen helpers)
protos/         the prototype designs (H, Hb, H2/Hb2, M2, E), plus mine_impl (design M; the judge
                and several suites use its pin reading) and proposal_impl (design P, used by
                analysis/lw13 and some probeA and probeB helpers)
adapters/       x_impl.py: design X, the repository's own wire_pins_to_net at the checkout under test
suites/         demo, probeA, probeB, probeC, llm-workflow, layouts_stock: the sample sets
analysis/       producers of ADR figures beyond the gate (dupref, slice1, followup2, crossban,
                manager, lw13)
caseidx/        the case index: 215 probe cases, with the scratch probe script each came from
baselines/      gate baselines, identity expectations and the script that derived them
reports/        the six reports the ADR cites
work/           git-ignored: runs/<run>/ (see "Where outputs go")
```

There is no `__init__.py` under `tools/`: the scripts keep their flat imports (`import rlib`,
`import judge3`), which `hpaths.bootstrap` makes work. The suites' probe scripts (`probeB/scripts`,
`probeC/decisions`, `probeC/geometry-misc`, ...) are the executable cases; `caseidx/*.json` names each
case's scratch scenario script as a `pr57pt/<dir>/<file>` label. Those scratch scripts are not
ported, so the labels are provenance, not paths.

## Requirements

- Python 3.11 or newer with the repository's dev extras: `uv sync --frozen --all-extras --dev`. The
  judge imports `tests/conftest.py`, which needs pytest and kiutils.
- `kicad-cli` with KiCad's demo projects and symbol libraries. kicad-cli resolves as the package
  resolves it (`KICAD_CLI_PATH`, then PATH, then the usual install locations); the demos and symbol
  libraries are taken from the same install (`share/kicad/demos` and `share/kicad/symbols`, or
  `SharedSupport/...` in the macOS bundle) unless `KICAD_DEMOS_DIR` or `KICAD_SYMBOL_DIR` names them.
  On Linux the demos come from the `kicad-demos` package.
- The baselines were taken with KiCad 9.0.8. Another version runs, but its results are not
  comparable with the baselines (see Known limits); `prepare.py` and `run.json` record the version.

`uv run python tools/routing_harness/hpaths.py` prints what resolves where (the checkout under test,
KiCad's directories, the run directory) and whether the guard passes. The examples below use POSIX
shell syntax for environment variables; in PowerShell set `$env:NAME = "value"` first.

## Two readings, and the one that is here

The pressure test judged every call twice:

- **cli** (measured): `kicad-cli`'s netlist of the file as written. This is the reading this harness
  takes.
- **gui** (emulated): `kicad-cli`'s netlist after an emulation of the KiCad 9 GUI's load-time cleanup
  had been applied to every sheet (eeschema itself was never driven). That emulation was a
  step-by-step port of KiCad's C++ source, which is GPL, and this repository is MIT, so it is not
  included: adding it is the maintainer's decision.

`core/gui_reading.py` is a stub with `AVAILABLE = False`; its `gui_load_proxy` raises
`GuiReadingUnavailable`. How the missing reading shows:

- judge3 skips it. A result's gui entry is `{"wrong": null, "unavailable": true, "error": "gui reading
  not available: ..."}` and `readings` lists `["cli"]`. The entry has no `delivered` key, so it can
  never turn a no-op into a false no-op, and `pre_satisfied` is computed over the cli reading only.
- Records carry `gui_wrong: null` (never `false`); a judged record's `gui_detail` says why. A reading
  that was not taken is never counted clean and never counted unjudged (`core/readings.py`).
- Summaries print `cli reading only (gui reading not available)` beside their figures, and
  `crossban_probe.py` prints `gui=n/a (gui reading not available)`.
- `run.json` records `"gui_reading": "not available"`. `core/gate.py` opens with
  `gui reading: not taken (wrong_gui is null, not 0); the "0 wrong in both readings" criterion cannot
  be evaluated by this run`, writes `wrong_gui: null` and `readings: ["cli"]`, and counts
  `gui_not_taken`.
- `analysis/lw13` judged a second, simpler GUI-equivalent copy (wires split at junctions, collinear
  overlaps merged) where the file before or after a write held such geometry. That emulation is
  excluded too; those records' `gui_view` says the reading is not available.

A maintainer who adds an emulation replaces that one module with `AVAILABLE = True` and a working
`gui_load_proxy`; nothing else changes. Every ADR figure quoted "in both readings" reproduces here only
in its cli half.

## Prepare the fixtures

```
uv run python tools/routing_harness/prepare.py --run r1
```

`prepare.py` builds the inputs the baselines pin down, from the installed KiCad, into
`work/runs/r1/fixtures/` and `work/runs/r1/demo/`: copies and derivatives of KiCad's demo projects,
and the synthetic layout bases. (Most probe scripts build their own synthetic sheets from KiCad's
symbol libraries, and a few read KiCad's demos directly.) It checks each against what the baselines
were taken on, in five steps:

| Step | What it builds | Pass |
|---|---|---|
| layouts stock fixtures | standalone copies of three demo sheets | 3 of 3 equal `baselines/fixtures_expected.json` |
| probeB vhp | `vhp.kicad_sym`, a demo-embedded symbol with hidden power pins | 1 of 1 equal |
| probeC perf | `suites/probeC/perf/build_fixtures.py`: 18 perf fixtures and its cross-checks | 18 of 18 equal, every cross-check true |
| demo | `suites/demo/run_sweeps.py --prepare-only`: 11 repathed demo sheets, candidate pins | same bytes and the same candidate pin list as follow-up 1 for all 11 |
| layout bases | `suites/layouts_stock/runner.py --only none`, then `check_bases.py` | 67 of 67 equal |

It prints each step's verdict, writes `work/runs/r1/prepare_report.json` (each step's counts, the
kicad-cli version, and drift from 9.0.8) and the children's logs to `work/runs/r1/meta/prepare_*.log`,
and exits 0 only if every step passed. A difference is a finding to report, never something to fix by editing an expectation.
`run.py` runs it first unless given `--skip-prepare`, and refuses to launch on a failure unless given
`--allow-drift` (recorded in `run.json`).

Measured 2026-10-10 on Windows with KiCad 9.0.8 and 16 logical CPUs: every step passes (3 of 3, 1 of
1, 18 of 18, 11 of 11 demo sheets with the same bytes and candidate lists, 67 of 67), in 5.5 minutes
on an otherwise idle machine and 12 with a test suite running beside it, most of it the layout bases.

## Run one suite

Each suite is one batch of `run.py`:

| Batch | Suite directory | What `run.py` starts | Summary script |
|---|---|---|---|
| `demo` | `suites/demo` | `run_sweeps.py --workers=5` | `suites/demo/summarize.py` |
| `probeA` | `suites/probeA` | `core/seqrun.py suites/probeA/jobs_probeA.json 1` | `suites/probeA/summarize.py` |
| `probeB` | `suites/probeB` | `driver.py 3` | `suites/probeB/summary.py` |
| `probeC` | `suites/probeC` | `runq.py` with `RUNQ_MAXP=3` (jobs from `jobs.txt`) | `suites/probeC/summarize.py` |
| `layouts` | `suites/layouts_stock` | `runner.py --workers 3 --resume` | `suites/layouts_stock/summary_build.py` |
| `llm` | `suites/llm-workflow` | `core/seqrun.py suites/llm-workflow/jobs_llm.json 2` | `suites/llm-workflow/summarize.py` |

To run one, prepare and gate it:

```
uv run python tools/routing_harness/run.py --run r1 --designs Hb2 --only probeC --wait
ROUTING_HARNESS_RUN=r1 uv run python tools/routing_harness/suites/probeC/summarize.py
uv run python tools/routing_harness/core/gate.py r1 Hb2
```

`run.py` writes `run.json` (the gate needs it), runs `prepare.py` (add `--skip-prepare` when
`prepare.py --run r1` already ran), starts the batch and, with `--wait`, exits non-zero if it failed.
A summary script reads the run `ROUTING_HARNESS_RUN` names and reports every design it finds in that
run's records; the gate is what the pass criteria are checked on. A suite script can also be run
directly on a prepared run, with the run and designs in the environment:

```
uv run python tools/routing_harness/prepare.py --run r2
ROUTING_HARNESS_RUN=r2 ROUTING_HARNESS_DESIGNS=X uv run python tools/routing_harness/suites/demo/run_sweeps.py wptn --limit=10 --workers=4
```

Give a direct run a run name of its own and keep one design set per run: the suites' resume logic
assumes it (the demo suite's resume key ignores the design, so a second design set skips calls, and
probeA rewrites its records file keeping only jobs whose record count matches the current designs).

Such a run has no `run.json` (only `run.py` and `smoke.py` write one), so it can be summarized but
the gate refuses it; to gate a suite, launch it through `run.py --only <batch>`. Every batch is
resumable: rerunning it skips the work it finished. The layouts stock runner, probeB `n06`, probeC's
perf scripts and the dupref scripts that read prepared fixtures (`disagree_test.py`, `isolate_test.py`,
`oracle.py`) fail when an input is missing and name the script that builds it. The demo suite instead
builds its fixtures in the run when they are missing and logs how they compare with the baselines;
only `--prepare-only` (what `prepare.py` runs) fails on a difference.

## The smoke subset

```
uv run python tools/routing_harness/smoke.py --run smoke
```

Eleven steps against the checkout under test, run in the order 1 to 8, 10, 9, 11 (step 9 reads the
records of steps 7 and 10). It stops at the first failure of a blocking step and writes
`work/runs/smoke/smoke_report.json`, with each step's output in `work/runs/smoke/smoke/`.

| Step | What runs | Pass |
|---|---|---|
| 1 paths and guard | `hpaths.py` | the checkout under test is the expected one, KiCad's demos exist, the gui reading is reported not available, the guard passes |
| 2 imports | the registry loads X, Hb2, Hb, H, M2, E; `proposal_impl`, `mine_impl` import | no exception |
| 3 compat54 | `place_component` from `core/compat54.py` and from the package, on fresh sheets; `_lib_unit_style` on every sub-symbol of `Device.kicad_sym` | same bytes with UUIDs masked; every value equal |
| 4 adapter shapes | `analysis/slice1/smoke_x.py`, `smoke_x2.py` | a write is OK, a second name and a bad label are REFUSED with `[H:names]` and `[H:validation]`, a repeat is NOOP; refusal lines parse as the adaptive policies expect |
| 5 judge self-check | `core/selftest_named_merge.py` | all assertions pass |
| 6 prepare and identity | `prepare.py` | every step passes (above) |
| 7 PI-15 and PI-17 | `pi_jobs.py` on five jobs, designs X and Hb2 | the four true duplicate references refuse with `dup_ref`, the unresolved symbol with `derived`, bytes unchanged, no ERROR |
| 8 crossing ban | `analysis/crossban/crossban_probe.py` | without the ban the cli reading shows the hazard (Q1 to Q5 merge /A and /N, Q4 and Q5 also split /N); with it every sequence matches its control; every gui field reads n/a |
| 10 demo slice | `run_sweeps.py wptn --limit=10` for design X | informational: no ERROR and no wrong write expected; differences from the scratch records are listed, not blocking |
| 9 gate | `core/gate.py` over the records of steps 7 and 10 (both must have run in this run) | first line says the gui reading was not taken, `wrong_gui` is null, `wrong_cli` 0 |
| 11 repo hygiene | ruff, ruff format, pyright, pytest collection, `uv build --sdist`, a forbidden-string scan, git status | lint and types clean; the default collection equals that of `tests/`; no harness path in the sdist; no user name, Windows user-profile path, home directory or home configuration path, venv path, em or en dash in any file here, and no path to a pinned export outside `reports/`; git status of `tools/routing_harness` (ignored files included, `work/` aside) unchanged since the smoke run started |

Options: `--steps 7,10,9` runs some steps; `--expect-collect N` also checks pytest's count against a
number taken before the harness existed; `--scratch-root <pr57pt>`, while the scratch directory still
exists, makes steps 8 and 10 also compare with the scratch crossing-ban output and demo records.

Measured 2026-10-10 on Windows with KiCad 9.0.8, on this port of base commit 8750f08: all eleven
steps pass, in 8 to 16 minutes, most of it step 6 (three runs: 7 min 55 s; 10 min 17 s on a CRLF
checkout; 15.5 min with a test suite running beside it). With `--scratch-root`, step 8's 30 result
lines match the scratch crossing-ban output and step 10's ten calls match the scratch X records.

## The full gate

```
uv run python tools/routing_harness/run.py --run gate-x --designs X
uv run python tools/routing_harness/core/watch_bad.py gate-x        # optional: each new bad record as it lands
uv run python tools/routing_harness/core/gate.py gate-x X --json tools/routing_harness/work/runs/gate-x/gate-x.json
```

`run.py` prepares the run and starts all six batches detached; run the same command again to resume
after an interruption (it refuses while a batch of that run is still alive, and refuses a run started
with other designs, another checkout or another commit). Stop a run by killing whole process trees: a
batch's children keep running when only the batch is killed. One design took about 1 h 50 min on 16
logical CPUs (measured 2026-10-03, 21:46 to 23:37, in the scratch run; the port runs the same jobs, so
expect the same order, but it was not timed here). A kicad-cli startup crash surfaces as an error in a
record: rerun those tasks. Restarting a batch reruns only what it has not recorded, and the demo,
probeC and layouts batches count a recorded error as done (inference from their resume code, not
run), so remove that task's output first, or use a new run.

Compare the JSON field by field with `baselines/gate_x_final2.json` (design X) or
`baselines/gate_rerun3_Hb2.json` (design Hb2). Every key keeps its scratch meaning; `readings`,
`offenders` and `gui_not_taken` are new, and `wrong_gui` is null here where the baselines have a
count. In the baselines `root` names the scratch run the figures came from. A sample with no records
for the design (a batch that did not run, wrote nothing, ran other designs, or whose jobs all crashed)
shows as `{"missing": ...}`, never as an empty gate; probeA's message then counts its HARNESS_ERROR
records.

### Pass criteria

From `reports/HANDOFF.md`, for one design:

- 0 wrong writes in the cli and gui readings, and no ERROR or HARNESS_ERROR;
- the only false no-op is DEC-23 (probeC `dec_d04` variant a), and the only unjudged call is probeB
  `h16 ... b_child_R32_3V3L:1`;
- refusal rates at or below the Hb2 column of `reports/dupref-report.md` section 5, or every rise
  explained.

Here the first criterion can be checked for the cli reading only: `wrong_cli` must be 0, and the gate
says the gui half cannot be evaluated. The gate lists each sample's offenders (record ids of false
no-ops, unjudged calls, ERROR and HARNESS_ERROR records and wrong writes), so the DEC-23 and h16
exceptions can be checked by id, not only by count.

The gate script itself was checked against the scratch records (measured 2026-10-10, during the
port): run on the records of the scratch Hb2 run and of the scratch X run, `core/gate.py` reproduces
`baselines/gate_rerun3_Hb2.json` and `baselines/gate_x_final2.json` with no difference in any
original key, and its offender ids are the ADR's exceptions (DEC-23, h16, and for X the five
stacked-name writes and two replays the status log describes).

## Where outputs go

Everything a run writes goes under `work/runs/<run>/` (`ROUTING_HARNESS_WORK` moves `work/`):

```
run.json              the setup: run, designs, FU2, checkout under test and its commit, kicad-cli
                      and its version, the demos directory, the gui reading, prepare result, batch pids
prepare_report.json   prepare.py's result
fixtures/             prepare-time inputs (copies and derivatives of KiCad's demos, layout bases)
demo/ probeA/ probeB/ probeC/ layouts_stock/ llm-workflow/
                      one directory per suite: records (records*.jsonl, probeB rec/, probeC and llm
                      parts/), finals, kept before/after files of wrong writes (wrong/), summaries,
                      per-process scratch (tmp/, work/)
meta/                 each batch's .out and .err, seqrun logs, prepare logs, watch_bad state
analysis/ lw13/ slice1/ crossban/ manager/ selftest/
                      outputs of the analysis scripts and the judge self-check
smoke/                smoke.py's step logs (smoke runs only)
```

A run directory holds the records of one setup, which is why `run.py` refuses to resume it under a
different setup. Nothing is written beside the code: entry scripts turn bytecode caching off before
importing `hpaths`, and children run with `-B`.

## Test another commit

Point `PT_REPO` at an export of that commit (it needs `mcp_server_kicad/` and `tests/conftest.py`):

```
mkdir -p tools/routing_harness/work/exports/54160c1
git archive 54160c1 mcp_server_kicad tests | tar -x -C tools/routing_harness/work/exports/54160c1
PT_REPO=tools/routing_harness/work/exports/54160c1 uv run python tools/routing_harness/run.py --run old --designs Hb2
```

Keep the export under `work/` (git-ignored, and outside ruff and pyright) or outside the checkout: an
export elsewhere in the checkout shows in git status and pyright checks it (measured 2026-10-10: 22
errors, all inside the export).

`hpaths` puts the export first on `sys.path` and refuses to run if `mcp_server_kicad` resolves anywhere
else. Design X is the target's own `wire_pins_to_net`, so it is meaningful only on a target from slice
1 on. On a target whose `wire_pins_to_net` still takes `auto_pwr_flag` (anything before #62, which
came before slice 1) `adapters/x_impl.py` refuses to load and says why; run a prototype there.

## Environment

All optional:

| Variable | Meaning |
|---|---|
| `PT_REPO` | checkout under test (needs `mcp_server_kicad/` and `tests/conftest.py`); default: the checkout holding this harness |
| `ROUTING_HARNESS_WORK` | work directory; default `tools/routing_harness/work` |
| `ROUTING_HARNESS_RUN` | run name, `[A-Za-z0-9._-]+`; default `default`; `--run` sets it |
| `ROUTING_HARNESS_DESIGNS` | comma list of designs (`X`, `Hb2`, `Hb`, `H`, `M2`, `E`); default `X`; `--designs` sets it |
| `ROUTING_HARNESS_FU2` | `1` restores follow-up 2's two demo recovery branches (`--fu2`); only to reproduce hybrid-report section 7 |
| `KICAD_CLI_PATH` | kicad-cli to use (otherwise the package's own resolution) |
| `KICAD_DEMOS_DIR`, `KICAD_SYMBOL_DIR` | KiCad's demos and symbol libraries when they are not under the install root |

## Frozen helpers and designs

`core/compat54.py` holds verbatim copies of `mcp_server_kicad/schematic.py` helpers as they were at
commit 54160c1 (the package's own code, frozen): `place_component`, `add_power_symbol` and the old pin
transform, plus names later commits moved or removed. The prototypes and the fixture builders use them
so that valid placements, and the fixtures built with them, stay what the baselines were taken with. It
adds one attribute to the package at run time (`schematic._lib_unit_style`, only if absent) and never
edits the package. If the package's behaviour for valid placements ever changes, the identity checks
in `prepare.py` are what will show it.

Designs are chosen by name (`core/registry.py`): `X` is the repository's own `wire_pins_to_net` at the
checkout under test (`adapters/x_impl.py`), with Hb2's `connect_pins`, `auto_place_decoupling_cap`,
`pin_positions` and `MODULE`, because slices 2 and 3 are not built. The adapter drops
`auto_pwr_flag`, rewrites reason codes `[code]` to `[H:code]` (the prefix the harness parses) and
re-raises each refusal as a plain `ToolError`, the class judge3 counts as REFUSED. `Hb2` is the
chosen prototype (`protos/h2_impl.py` with `CROSS_BAN = True`); `Hb`, `H`, `M2` and `E` are the other
prototypes. Design K (the coarse refusal model of follow-up 1) is not ported.

## ADR figures and how to reproduce them

All of these reproduce only in the cli reading.

| ADR figure | Producer | Recipe |
|---|---|---|
| 16 of 36 layouts shorted | `analysis/lw13` | only against a pre-slice-1 target, since its design B is the target's own tools: `PT_REPO=<54160c1 export>`; `run_generated.py 6`; `analyze_generated.py` |
| wrong pin in 4 of 12 orientations; a junction keeps only the start half | `analysis/manager/mgr_verify.py` | claim 1 reads the target's pin transform, so it reports 4 of 12 only with `PT_REPO=<54160c1 export>` (on 8750f08 all 12 are ok: measured 2026-10-10); claim 2 on any target |
| M2 24 wrong, E 0, Hb2 0; rates 17.3% / 36.9%, 20.1% / 100% | suites and `core/gate.py` | `run.py --designs M2,E,Hb2`; `gate.py <run> M2` etc. (generated/skill and demo_wptn rates; `wrong_cli`) |
| E leaves 99 planned pins off | `core/gate.py` | an E run; `planned_off` of layouts (adaptive) and llm (adaptive) |
| crossing hazard (labels and junctions merge /A and /N) | `analysis/crossban/crossban_probe.py` | any target |
| the ban costs 20 connect_pins refusals | suites and summarizers | `run.py --designs H,Hb`; the per-batch same-input comparisons (inference from hybrid-report section 3, which names the batch summaries as its sources) |
| repo `add_label` wrong in 41 of 67 | `analysis/followup2/agg_fallback.py` | `run.py --designs M2,H,Hb,E --fu2`, then the probeC coverage rerun (`mk_fball_jobs.py`, `core/seqrun.py`), then `agg_fallback.py` |
| 63 dup_ref calls, 58 lift (19, 27, 12), PI-15's 4 refuse, tiny_tapeout U3 | `analysis/dupref/lift.py`, `by_reference.py` | a design-Hb run and a design-Hb2 run; `lift.py --old A:Hb --new B:Hb2` |
| cases A and B, mixed parts, pad copies never joined by the netlist, ERC different_unit_net | `analysis/dupref/isolate_test.py`, `disagree_test.py` | `--run` of a prepared run |
| IC14 is 20 symbols under 4 library names | `analysis/dupref/oracle.py` | `--run` of a prepared run |
| about 8 s per call on vme-wren (design H) | `analysis/crossban/perf_h.py` | timing, machine-dependent |
| 4,816 inputs, gate X | suites and `core/gate.py` | `run.py --designs X`; `gate.py <run> X`; compare with `baselines/gate_x_final2.json` |
| five stacked-name writes re-judged clean | `analysis/slice1/rejudge_x_names.py` | on the X run |
| probeC 31.5% vs 30.0%, 11 extra refusals are stub lengths | `core/gate.py`; `analysis/slice1/diff_probeC.py` | an X run against an Hb2 run |
| skill policies 26 of 147 vs 40; 169 of 1,196 vs 302 | `core/gate.py` `planned_off` | an X run against an Hb2 run |
| inputs the later commits can reach | `analysis/slice1/scan_fix_reach.py`, `scan_multiname.py`, `scan_round3.py` | on a run's fixtures and KiCad's demos |
| round 3: 73 sweep calls and 9 probe scripts, 247 records, call for call | `analysis/slice1/diff_all.py` | between two X runs (the subset assembly of that round is not ported; a full X run covers the same inputs) |
| the model's two views agree on 93 demo sheets; 42 sampled calls | not this harness | the repository's own tests |

## Known limits

- The gui reading is not available (above), and there is no KiCad 10 reading, as in the scratch runs:
  KiCad 10 files and the jumper refusal are verified by no reader here.
- probeB `h16` uses a KiCad 10 `(power local)` symbol that kicad-cli 9 cannot load, so it has no
  verdict for any design and stays the one expected unjudged call.
- DEC-23 is a judge limit: no netlist lists `#` (power symbol) nodes, so the correct no-op on a power
  symbol's pin shows as a false no-op (`analysis/followup2/dec23_check.py` shows the no-op is correct).
- judge3's blind spots: label text that mimics an auto name (`Net-(...)`), and node sets listed twice
  (pad copies on two unconnected nets), where it skips rename detection.
- The identity checks and baselines are tied to KiCad 9.0.8's demo bytes and kicad-cli 9.0.8. CI's
  KiCad jobs run other versions (the Linux KiCad 9 leg ran 9.0.9 on 2026-10-10; the others run KiCad
  10), so results there are not comparable with the baselines; `run.py` records the version and
  `prepare.py` reports the drift.
- The prototypes use the target's package helpers except those `core/compat54.py` freezes, so a
  later change to one of them can change a prototype's message. One is measured: at this commit the
  prototypes' `auto_place_decoupling_cap` refuses a derived library symbol with code `validation`
  where a 54160c1 target gives `derived`, because the package's library copy now refuses it first
  (measured 2026-10-10 with `MCU_Microchip_ATtiny:ATtiny85-20P` through Hb2 and M2). Both refuse;
  only the code differs.
- Design X's crash-window and write-timing probes (probeC composite `c08_window_timing`, `cmp_child`,
  `inproc_hooks`) hook `mcp_server_kicad._shared._atomic_write` or Hb2's module (`MODULE`). The
  package's schematic binds `_atomic_write` by name, so for X those probes never see the repository's
  own write. Inherited from the scratch adapter, not fixed here (inference from the code, not measured).

## Not ported, and why

- The gui-reading emulations (above: vcommon's and lw13's), and `h_build/*.cpp` (verbatim KiCad
  source, never copied).
- Design K (`coarse_impl.py`), the launchers (`py`, `launch3.ps1`, replaced by `run.py` and `hpaths`),
  and the scratch-directory orchestration of later reruns (`dupref/make_rerun*`, `switch_hb2`,
  `export_s1`: replaced by run directories, the design registry and `run.py`).
- One-shot fixture builders whose inputs (the original probe directories) are gone and whose outputs
  are committed or rebuilt by `prepare.py` (`pinsets.py`, `pi_prep.py`, `make_*`, `_port_from_followup1`).
- Earlier iterations and provenance copies that no cited figure depends on (the `dupref` tests superseded
  by `isolate_test.py` and `disagree_test.py`, `layouts_stock/src/measure_*`, `counterfactual.py`,
  `d_tmp.py`, the code-size scripts).
- Every result: records, parts, netlists, logs, pickles, copies of KiCad's demo schematics and boards,
  the libraries the probeB scripts write, and the other files the scripts wrote next to their code
  (the port redirects those writes under `work/`).

## Licence note for the maintainer

KiCad is GPL and this repository is MIT. The gui emulations were written from KiCad's C++ source and
are excluded for that reason. The prototype designs cite KiCad source line ranges for rule statements and
small formulas (for example a 2x2 matrix product), the same practice as the package's own
`_connectivity.py`, and they are included. That classification is an inference, not a legal
conclusion; it is the maintainer's call.

## Editing the harness

Keep scripts close to the scratch originals; the smoke subset is the regression check. Every entry
script starts with the preamble in `hpaths.py` (a bare `import hpaths` in library modules), takes
every path from `hpaths`, writes only under `hpaths.RUN_DIR`, and runs children as
`[hpaths.PYTHON, "-B", script, *args]` with `hpaths.child_env()`. Files use LF, on every checkout
(`.gitattributes`), because `baselines/derive.py --check` compares bytes. `smoke.py` step 11 fails on
a user name, a Windows user-profile, home directory or home configuration path, or an em or en dash in
any file here, on a path to a pinned export outside `reports/`, and on any change a smoke run makes to
git status under `tools/routing_harness` (ignored files included, `work/` aside).
