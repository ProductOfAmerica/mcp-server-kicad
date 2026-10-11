# Coarse refusal model vs section 5 design: measured

2026-10-02. Repo main at 54160c1 (untouched; both scratch worktrees removed). kicad-cli 9.0.8.

Tags: **measured** = kicad-cli 9.0.8 netlist; **emulated** = judge3's port of the KiCad 9.0.8 GUI load-time cleanup, then kicad-cli; **sourced** = KiCad or repo source; **inferred** = reasoning. All paths are under `%TEMP%\pr57pt\coarse\` unless absolute. Every figure below was produced by a re-run in this pass, not carried over from the first report.

## 1. What was compared

- **M2** = `m2_impl.py`: the first report's prototype (`mine_impl.py`) plus the two section 5 decision rules it lacked:
  - no-op decided before refusal;
  - proceed when only the possible model reaches N.
- **K** = `coarse_impl.py`: a standalone copy of M2 in which only the two models change.
  - **No-op model:** narrow. It is M2's certain model minus three rules: the two-lines label rule, sheet pins as conductors, and directive labels.
  - **Refusal model:** coarse, with a 0.05 mm margin.
    - Every point item within the margin of a point or line joins it.
    - Crossing lines join.
    - Graphic 2-point polylines are lines.
    - Same-text names close transitively.
    - Names: every label kind (directive labels included), every power symbol's Value (PWR_FLAG included), and every power_in pin name, visible or hidden.
    - The call refuses outright if the component reaches a bus, a bus entry, a sheet pin, `${` text, a jumper symbol, a unit-0 pad whose units are not all on this sheet, or a symbol whose instances disagree on the unit.
    - A derived or unresolved library symbol anywhere on the sheet refuses the whole call.
- **Choices beyond the brief:** the builder took 20 (K-C1 to K-C20), listed in K's docstring. Two are less conservative than possible:
  - K-C14: polylines of 3 or more points are ignored, so that the touch rule stays identical to M.
  - K-C15: names are compared on raw text.
- **Shared, not under test:** the transform, no junctions, the overlap ban, the strict touch rule, pad identity, no auto label, one write, validation, the no_connect-type refusal, and no-op before refusal.
- **Judge:** `judge3.py`, one judge for every sample.
  - Delivery and false no-ops are checked with a probe (an off-page test point labelled N), so two-name nets and names containing '/' are judged correctly.
  - Two judge bugs were found and fixed during the runs: a phantom rename on duplicated node sets, and per-pad groups flagging a requested multi-pin join. Every batch re-judged or confirmed 0 affected records.
  - judge3 does not flag two things: a merge of two nets that already carried user names, and a pad left in two nets when it was already in two nets before the call. Every batch checked both separately.

## 2. Wrong writes and false no-ops

Wrong writes are counted in cli/gui (measured/emulated). Sources:
- Probe A: `rerun\probeA\summary.md`.
- Probe B: `rerun\probeB\summary.md`.
- Probe C: `rerun\probeC\summary.md`, plus `rerun\probeC\notes\llm-workflow.json` for llm-workflow.
- Generated layouts and stock: `rerun\layouts_stock\summary.md`.
- Demo sweeps: `rerun\demo\summary.md`.

| Sample | Calls per design | M2 wrong | K wrong | Extra checks (judge3-blind) |
|---|---|---|---|---|
| Probe A: pin-identity, junctions, inert, labels | 647 | 9 / 9 | 7 / 7 | none |
| Probe B: buses, hierarchy, naming, units-pads | 798 | 2 / 2 | 0 / 0 | M2: 5 named-net merges (HIER-06), 6 pad-in-two-nets writes; K: 0 |
| Probe C: geometry, composite, decisions, perf, llm-workflow | 1,299 (M2) / 1,307 (K) | 2 / 2 | 0 / 0 | M2: the same 2 calls (DEC-14) |
| 36 generated layouts, two policies | 655 (M2) / 709 (K) | 0 | 0 | 0 |
| Stock: 112 pins, 21 pairs, 20 star nodes, 11 junction fixtures | 202 | 0 | 0 | 0 |
| Demo sweeps: 718 pins, 244 pairs, 160-pair fallback sample | 1,122 (M2) / 1,127 (K) | 0 | 0 | 0 |

**Every wrong write:**
- **Both designs, 7.** Neither model covers either cause (both rated rule-level in the first report):
  - 4 merges of two symbols sharing a Reference (PI-15);
  - 3 class changes from a rule area with a netclass flag (INERT-05).
- **M2 only, 13 harmful writes.** K refused each of them:
  - 2 merges into an unresolved symbol's pins, which KiCad stacks at the symbol origin (PI-17). K refused through its sheet-wide unresolved check.
  - 2 renames and splits from wiring one copy of a unit-0 pad whose other copy is on another sheet (UP-05), plus 2 splits shorting RN1:1 across sheets (DEC-14). K refused with `unit0_unplaced`.
  - 5 merges of a net already named +5V through a sheet pin into GND or N1 (HIER-06), where the sheet pin lies on a wire interior. M2's model does not attach it. K's 0.05 mm margin does, and K then refused with `sheet_pin`.
  - 6 further M2 writes left a pad listed in two nets (HIER-14, UP-05). judge3 counts them clean.
- **False no-ops:** 0 in every sample for both designs. The one flagged record (DEC-23) is a judge limit: kicad-cli lists no node for a `#PWR` pin.
- **Redundant writes:** the cost of the narrow no-op model. Every one, in every sample, was clean and delivered (the brief's question 1):
  - Probe A: 4 per design.
  - Probe B: 3, all from M2 (it wired the unwired copy of a pad).
  - Probe C: GM-13, DEC-06 and LW-12 (4 in llm-workflow alone), in both designs.

## 3. Refusal rates

Rates are first attempts, measured.

| Sample | M2 | K | K-only refusals | M2-only |
|---|---|---|---|---|
| Generated layouts, skill policy (294 first attempts) | 17.3% | 27.2% | 23 on identical states; 29 by counterfactual replay | 0 |
| Planned pins left off their net, skill policy | 25.3% | 42.8% | | |
| Planned pins left off their net, adaptive retries | 0% | 17.9% | | |
| Stock, 112 existing pins | 0.9% | 6.2% | 6 | 0 |
| Stock, 21 pairs | 38.1% | 42.9% | 1 | 0 |
| Star nodes and junction fixtures | identical | identical | 0 | 0 |
| Demo wire_pins_to_net, 718 pins | 36.9% | 44.8% | 57 | 0 |
| Demo connect_pins, 244 pairs | 65.6% | 67.6% | 5 | 0 |
| Fallback sample: wire_pins_to_net on 160 refused pairs | 1.25% | 26.7% | 42 | 0 |
| Probe A / B / C cases | | | 6 / 26 / 69 | 0 / 0 / 2* |

\*Probe C's 2 M2-only refusals came after the two designs' files had already diverged, so they are not a same-input comparison.

The demo-sheet refusals that both designs make are mostly NC flags (167) and no_connect-type pins (96) on finished designs. They are not model costs.

### Why K refuses (K-only, by the single switch that lifts the refusal; measured inside K, re-checked by lift runs)

| Cause | Count | Where |
|---|---|---|
| Visible power_in pin names count as names (`POWER_IN_ALL`) | 48 layouts + 38 probe C + 2 probe A + 4 demo | MCU VDD/VSS/VCC pins on generated layouts and hand designs; tinytapeout J15 SHIELD |
| Plain crossings join (`CROSS_JOIN`) | 58 demo + 5 layouts + 10 probe C | StickHub, in_out_conn, power-supply-1, tinytapeout |
| `unit0_unplaced` | 15 B + 9 C + 1 A | |
| `sheet_pin` | 9 B + 6 C | |
| Directive labels count as names | 1 | |
| Bus or bus entry | 1 B + 6 C | |
| Unresolved symbol (K-C4) | 2 A | |
| Lifts with nothing | 22 C + 1 B | mostly the unhandled-item refusals; 2 on vme-wren/clocks need several switches |

**Lift runs and fallbacks** for K-only refusals, each on a fresh copy and judged in both readings:
- **Lift runs** (the same call with the switch off): 53 of 53 clean and delivered on the layouts and stock samples. On the demo sheets, M2 wrote all 62 cleanly.
- **Label on the pin** (the repo's `add_label` at the KiCad-true pin end): 213 of 216 clean and delivered. The 3 failures are cases where the refusal was right and the fallback is unsafe too:
  - NAM-04: a sheet-pin promotion split.
  - DEC-14 ×2: the cross-sheet pad short.
- **`wire_pins_to_net` with a shared name** (the fallback for a refused connect_pins): K refused it too in every tested case (5 demo, 9 probe C, 5 probe B, 1 stock). For K, this documented fallback does not recover connect_pins.

## 4. Where K's protection actually came from

All 13 of the M2 harmful writes K avoided trace to two of K's features:
- **The unhandled-item refusals:** unit-0 pad not fully placed, unresolved symbol anywhere, sheet pin.
- **The margin attach of a sheet pin to a wire interior** (HIER-06): one place where M2's precise rule set had a hole. The first report's section 5 already names this hole, but the M2 prototype never implemented the fix.

None came from the two coarse rules that dominate K's cost:
- visible power_in pins as names: KiCad names a net only through hidden or power-symbol power_in pins (sourced 9.0 sch_pin.cpp:323-327; measured N10);
- plain crossings as joins: no KiCad reader joins a crossing without a junction or label (measured G03; sourced 10.0).

On every sample, every refusal from those two rules lifted cleanly. That is the measured basis for calling them false refusals; that no future KiCad release changes either rule is inferred, not measured.

## 5. Simplicity

Measured, from `linecount.out`:

| File | Code lines | Physical lines |
|---|---|---|
| `mine_impl.py` | 1,229 | 1,565 |
| `m2_impl.py` | 1,278 | 1,669 |
| `coarse_impl.py` | 1,416 | 1,923 |

The diff from M2 to K is +268 / -130 code lines.
- K drops 9 rules: exact-point-only joins, the 2 IU capture radius, the overlap join, the P0/P split, port and unknown-name categories, the two-lines rule, sheet pins and directive labels as conductors, and the K9 attach block list.
- K adds 9: one margin for every join, point-on-line joins, crossing joins, name closure, directive and PWR_FLAG and every-power_in names, 7 unhandled-item refusals, and the sheet-wide unresolved check.
- About 33 lines of K are attribution support. Without them K would still be about 105 lines larger (inferred).
- The margin needs a spatial index, and the name closure needs its own pass. **K has fewer special cases but is not smaller.**

Performance (measured, loaded machine) does not separate the two:
- Model builds on the 5 largest demo sheets: M2 0.015 to 0.098 s, K 0.018 to 0.111 s.
- A 200-pin call: M2 5.68 s, K 4.22 s.

## 6. Recommendation

**Adopt K's refusal structure with two of its conservative choices reverted. Keep the narrow no-op model.**

Concretely:
1. Keep K's coarse geometry: one margin for every point-to-point and point-to-line join, and graphic lines as lines. This is the part that caught M2's sheet-pin hole.
2. Keep every outright refusal K has for items the model does not handle: bus, bus entry, sheet pin, `${`, jumper groups, a unit-0 pad not fully placed, units that disagree, and an unresolved or derived symbol anywhere. These are the source of all of K's measured protection.
3. Revert crossings-join. Instead, add one rule to the touch rule: new geometry may not cross an existing line. This closes the gap the builder found: under K-C1 a new crossing is an unevaluated possible join.
4. Revert every-power_in-is-a-name. Use KiCad's rule (hidden, or on a power symbol), and drop PWR_FLAG as a name; PWR_FLAG's pin is power_out (measured N15).
5. Keep the narrow no-op model. Its only cost, a redundant label, was clean in every case.

**Evidence and its limits:**
- Lifting either switch alone cleared every K-only refusal it was responsible for, cleanly (measured, section 3).
- Turning both off together was run only as a model recomputation on the demo sheets, where all 62 refusals lifted. A full re-run of the recommended variant was not done. Its refusal rates are expected near M2's (17.3% vs 27.2% on generated layouts, 36.9% vs 44.8% on demo wire_pins_to_net), but that is **inferred**: run the same samples on it before adoption.
- Item 3 is a new restriction. Its refusal cost on dense sheets is unmeasured.
- Shared defects neither model fixes: duplicate references (PI-15) and rule areas (INERT-05) still need their own rules (first report, section 5).

## 7. Not run, partial, or limited

- **Probe C's llm-workflow category** finished after the first draft of this report: 560 calls for M2 and 568 for K, 0 wrong writes, 0 false no-ops, and 36 K-only refusals.
  - All 36 are `[K:names]`: 34 lift with POWER_IN_ALL=False; 2 on vme-wren/clocks need several switches.
  - The label fallback was clean and delivered on all 36.
  - With adaptive retries, K leaves 15 planned pins off-plan across the hand designs; M2 completes every plan.
- **auto_place_decoupling_cap:** run only in probe C composite (56 OK and 30 refused per design, 0 wrong); no other sample calls it.
- **KiCad 10:** no reading. The jumper refusal was never checked against a reader, because kicad-cli 9 cannot load the token.
- **eeschema was never driven:** all GUI claims are emulated.
- **Other omissions:** CMP-11 (add_power_symbol) and P-internal perf measurements were not re-run. Probe B skipped 9 scenarios no case names; full lists are in each batch's `not_rerun` notes.

## 8. Verification of the two claims

### 8a. Auto PWR_FLAG crash

Source: `v1_pwrflag\`, scripts s1 to s5. The claim is true and its cause is as stated.

- **Repro:** `add_power_symbol(power:+5V)`, place LM7805, then `wire_pins_to_net([U1:VI], 'VIN')`. Raw `kicad-cli sch erc` returns 3221225477 (0xC0000005) in 3 of 3 runs. A netlist export returns 0 (measured).
- **Cause:** `add_power_symbol` copies the PWR_FLAG library entry under the bare name `PWR_FLAG`, at schematic.py:770-774 and 1456-1468. The auto flag then finds that entry by bare-name match (1240, 1321-1330) and writes its template (1124-1125) with lib_id `power:PWR_FLAG` and no lib_name, which resolves to nothing (sourced and measured).
  - Only ERC crashes, not a plain load.
  - Any unresolved symbol triggers it (measured control g). The comment at schematic.py:1475-1477 overstates it as a load crash.
- **Fix:** about 3 lines (`lib_name` in the template, plus setting it). The output then differs from the crashing file by one line, and ERC returns 0 on 5 sequences (measured, s3).
- **Removing the parameter does not rest on the crash.** With the crash fixed, the per-call flag still adds one `pin_to_pin` ERC error whenever the net already has a driver or a flag. That includes the documented rail flow, `add_power_symbol` then wire to the rail (G1a, G1b, G2a, G2b; measured, s4). The same evidence supports defaulting the parameter to False just as well as removing it (inferred). That choice is the user's.

### 8b. connect_pins refusing two differently named nets

Source: `v2_connect\`. The suite was run in an instrumented worktree, since removed.
- **Tests:** no test changes. 1,167 tests passed both instrumented and not; there are 19 connect_pins calls, none on two named nets and none already on one net (measured).
- **Skill promises:** one breaks in effect. `skills/schematic-design/SKILL.md:99` sends a failed connect_pins to wire_pins_to_net. That fallback performs the very merge being refused today (measured: one net /NET_A, ERC multiple_net_names), and it is refused under the new design. The skill needs a "stop and report" step for this refusal.
- **Holds only conditionally:** `SKILL.md:271` and `:170-171`.
- **Tests that change for the report's other connect_pins changes** (listed separately in `v2_connect`):
  - auto label: 4;
  - junctions: 2;
  - ToolError instead of ValueError: 3.
