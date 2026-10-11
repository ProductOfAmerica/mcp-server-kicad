# Hybrid H and empty-space E against M2: measured

2026-10-03. The repo was not touched. It moved to another branch during the work, so every run used a read-only `git archive` export of 54160c1 (`%TEMP%\pr57pt\base54160c1`); judge3 refuses to run on anything else. kicad-cli 9.0.8. Paths are under `%TEMP%\pr57pt\coarse\`. Tags: **measured** = kicad-cli netlist; **emulated** = judge3's port of the GUI load-time cleanup, then kicad-cli; **inferred** = reasoning. Every figure comes from runs made on 10-03 with the fixed judge (section 2).

## 1. Result

- **H passes the gate:** 0 wrong writes and 0 false no-ops on every sample, in both readings. The one flagged no-op is DEC-23, a judge blind spot; a separate measurement shows that no-op was correct.
- **Hb (H with the crossing ban) passes too.** The ban prevents one concrete failure that needs another tool to act later (section 6). It costs 20 connect_pins refusals.
- **E makes no wrong writes, but refuses most edits on existing designs.** Demo `wire_pins_to_net` refusal rate: E 100%, H 37.7%. Pins left off their net with adaptive retries: E 99, H 0.
- **Recommendation: H, with the ban.** Settle H-C1's duplicate-reference reading first; it causes 43 of H's 73 costly refusals (section 5). Give the skill section 7's rule for connect_pins refusals.

## 2. Builds and judge

**H** (`h_impl.py`, H-C1 to H-C19; **Hb** = `hb_impl.py` loads it with `CROSS_BAN = True`). It is K with KiCad-rule names, no crossing joins, and K's margin, unhandled-item refusals and narrow no-op model kept. Two refusals are new:
- **H-C1, duplicate references:** checks the whole hierarchy, by Reference and every instance reference. A shared reference is allowed only when every symbol has the same library name, byte-identical library entries and a distinct unit.
- **H-C2 to H-C5, netclass:** labels with a Netclass property, and rule areas with a directive. H refuses unless every joined part certainly carries one class set.

**E** (`e_impl.py`, E-C1 to E-C18). Four choices matter below:
- **C7:** only the 1x stub is tried. A longer stub contains the 1x segment, so 2x and 3x cannot pass when 1x fails.
- **C10:** a pin whose implicit name equals N is still wired.
- **C12:** other sheets are found from the project root and from the directory.
- **C16:** connect_pins has no no-op.

**Judge** (`judge3.py`) adds two checks:
- `named_merge`: two user-named nets joined, even inside the requested union.
- `req_pad_multi`: a requested pad left in two nets. Delivery now needs each pad in exactly one net.

**The `named_merge` fix.** It stripped sheet paths before testing for auto names, so `unconnected-(U1-PB7/PB8-Pad1)` counted as a user name. Fixed and regression-tested (`rerun2\meta\test_named_merge.out`). Every record judged before the fix is in `rerun2\_superseded_pre_judgefix\`, and all six batches were re-run.

**Calibration.** On probeB the judge flags exactly the M2 writes follow-up 1 had to find by hand (`rerun2\probeB\summary.out`): 5 HIER-06 named merges (+5V into GND or N1), 6 pads left in two nets, 2 UP-05 renames or splits.

**Self-tests, re-run on the fixed judge:**
- H: 88 calls, 0 wrong, 0 false no-ops (`h_build\selftest_h.out`).
- E: 69 calls, 0 wrong, 0 false no-ops (`e_build\selftest_e.out`).

**Harness fixes made in this pass:**
- the layouts summary keyed stock items without the case (fixed; it had hidden 3 H-only refusals);
- a probeB shim shadowed the harness alias (16 naming jobs re-run);
- probeC perf had not been ported to four designs (ported).

## 3. Gate: wrong writes and false no-ops

cli and gui agree on every row. Sources: `rerun2\` `probeA\summarize.out`, `probeB\summary.out`, `probeC\summary.md`, `llm-workflow\summarize.out`, `layouts_stock\summary_auto.md`, `demo\summary.md`.

| Sample | Calls per design | M2 wrong | H | Hb | E |
|---|---|---|---|---|---|
| Probe A: pin-identity, junctions, inert, labels | 673 | 9 | 0 | 0 | 0 |
| Probe B: buses, hierarchy, naming, units-pads | 798 | 13 | 0 | 0 | 0 |
| Probe C: geometry, decisions, composite, perf | 739 (corrected 10-03, see below) | 2 | 0 | 0 | 0 |
| llm-workflow: 4 hand designs, 2 policies | 560 to 564 | 0 | 0 | 0 | 0 |
| 36 layouts, stock, star, junction fixtures | 857 (E 887) | 0 | 0 | 0 | 0 |
| Demo: 718 pins, 244 pairs, 244 fallbacks | 1,206 | 0 | 0 | 0 | 0 |

- **M2's 24 wrong writes:** PI-15 ×4, PI-17 ×2, INERT-05 ×3, HIER-06 ×5, HIER-14 ×4, UP-05 ×4, DEC-14 ×2. H refused 21 of them and wrote the 3 INERT-05 calls cleanly. Hb and E made none of them.
- **False no-ops:** 1 each for M2, H and Hb, all DEC-23 (`wire_pins_to_net([#PWR02:1], 'VCC')` on a VCC power symbol). No netlist lists a `#` node. In `rerun2\meta\dec23_check.out`, a resistor wired to `#PWR02`'s pin end lands on VCC, so the no-op is correct.
- **Redundant writes:** every one is clean (probeA, probeB, probeC, llm-workflow).
- **Correction (10-03, found by `dupref\gate.py`):** probeC first read 741/742 calls. A restarted job queue ran `gm_a2_facing` twice at once, so 3 calls per design were recorded twice with identical outcomes. Counts and rates above are on unique calls; no verdict changed.

## 4. Refusal rates (first attempts) and pins left off their net

| Sample | M2 | H | Hb | E |
|---|---|---|---|---|
| Probe A, 647 strict calls | 26.3% | 27.5% | 28.3% | 48.7% |
| Probe B, 796 | 17.3% | 23.7% | 24.4% | 90.2% |
| Probe C, 739 (corrected 10-03) | 27.2% | 30.6% | 31.0% | 63.1% |
| llm-workflow, 392 | 27.8% | 27.8% | 28.6% | 32.1% |
| Generated layouts, 294 | 17.3% | 17.3% | 17.3% | 20.1% |
| Stock pins 112 / pairs 21 | 0.9% / 38.1% | 3.6% / 38.1% | 3.6% / 42.9% | 98.2% / 95.2% |
| Star 39 / junction fixtures 11 | 51.3% / 36.4% | 51.3% / 36.4% | 51.3% / 36.4% | 51.3% / 54.5% |
| Demo wire_pins_to_net 718 / connect_pins 244 | 36.9% / 65.6% | 37.7% / 65.6% | 37.7% / 67.2% | 100% / 100% |
| Fallback sample (160 pairs M2 refused) | 1.25% | 3.1% | 3.1% | 100% |

Planned pins left off their plan net, skill policy / adaptive retries:

| Sample | M2 | H | Hb | E |
|---|---|---|---|---|
| Generated layouts (1,196 planned) | 302 / 0 | 302 / 0 | 302 / 0 | 373 / 96 |
| Hand designs (147 planned) | 40 / 0 | 40 / 0 | 40 / 0 | 40 / 3 |

- **Skill-policy losses:** ambiguous pin names, which every design refuses with the same text.
- **E's adaptive losses:** 93 of its 99 are stacked power pins of one part, where a different pad's pin end sits at the same point (ATmega48 GND 8 and hidden 22; STM32 VSS 23/35/47); E refuses each as already connected. The other 6 have no refusal of their own; the calls carrying them were refused for a stacked pin.

## 5. Same-input refusal sets, H against M2

M2-only refusals against H: **0 in every sample.**

| Sample | H-only | M2 wrote wrong there | M2 wrote cleanly | M2 no-op, correct |
|---|---|---|---|---|
| Probe A | 8 | 6 (PI-15 `dup_ref` 4, PI-17 `derived` 2) | 2 | 0 |
| Probe B | 47 | 10 (`sheet_pin` 5, `unit0_unplaced` 5) | 10 | 27 |
| Probe C | 27 | 2 (`unit0_unplaced`, DEC-14) | 25 | 0 |
| Demo wire_pins_to_net / stock pins | 6 / 3 | 0 | 9 | 0 |
| Every other sample | 0 | | | |

Of H's 91 extra refusals, 18 blocked a wrong write. The other 73 cost something:
- **`dup_ref` 43:** 27 of these were correct M2 no-ops. Every one is distinct units of one part stored under library-name variants (vme-wren IC14, IC20 and W1, plus a demo U1), which H-C1 refuses.
- **`unit0_unplaced` 13.**
- **`sheet_pin` 10** and **`bus` / `bus_entry` 5:** K's blanket refusals, on cases M2 wrote cleanly.
- **`netclass` 1** (LBL-04) and **`names` 1** (GM-06: the 0.05 mm margin reaches a near-miss label).

Follow-up 1's large K costs are gone. H refused nothing M2 accepted on the generated layouts, hand designs or demo connect_pins.

## 6. Crossing ban: the failure it prevents, and its cost

Source: `h_build\crossban_probe.out`, re-run 10-03.
- **Geometry:** H's stub from R1:1 (101.6, 97.79) to (101.6, 95.25) crosses net A's wire, which runs along y = 96.52, at X = (101.6, 96.52).
- **Sequence:** H `wire_pins_to_net([R1:1], 'N')` is clean. A later repo call then puts a connection point on X:
  - `add_label('A')`, `add_label('B')` or `add_global_label('A')` at X: the label attaches to both lines, and /A merges with /N in cli and gui (`bad_merges` plus `named_merge`). The control without H's stub is clean, apart from a global-label rename.
  - `add_junctions([X])`, or `add_wires` from X, where the repo auto-inserts a junction: gui merges /A and /N; cli merges them and splits /N.
  - With the ban, H labels the pin instead, and every sequence matches its control.
- **What the ban covers:** H's own model refuses a later H call at X (Q9). The same hazard already exists at every pre-existing crossing (S6); the ban only stops H adding new ones.

**Cost (Hb-only against H, same input):** 20 `connect_pins` refusals, all `touch`:

| Batch | Refusals | Recovered by Hb's shared-name `wire_pins_to_net` |
|---|---|---|
| demo | 4 | 4 |
| probeA | 5 | 2 |
| probeB | 5 | 5 |
| probeC | 2 | 2 |
| llm-workflow (star) | 3 | 3, through the star script's shared name 'ADC' |
| layouts | 1 | not measured |

H wrote all 20 cleanly. In `wire_pins_to_net` the ban never refused; it only swapped a stub for a label on the pin.

## 7. After an H connect_pins refusal: what recovers

Source: `rerun2\meta\agg_fallback.out`, 332 H refusals. Each recovery ran on a fresh copy of the before-state. probeC's 53 missing fallbacks came from a coverage re-run, used only where the UUID-normalised before-state matched.

| H code (refusals) | (a) H `wire_pins_to_net` with a fresh shared name | (c) repo `add_label` at both pin ends |
|---|---|---|
| `touch` (236) | 198 recover, 38 refused | 235 recover; 1 flagged wrong is DEC-23's invisible `#` node |
| `names` (29) | 0 | 18 wrong |
| `sheet_pin` (22) | 0 | 13 wrong |
| `bus` / `bus_entry` (16) | 0 | 10 wrong |
| `copies` (7) / `dup_ref` (6) | 2 / 0 | 7 / 6 recover |
| `nc_flag`, `nc_type`, `resolve`, `text_var` (16) | 0 | unsafe or not delivered |

**The 38 `touch` cases where (a) refused:**
- **26 were `names`,** because one pin is already on a named net.
  - (b), wiring the other pin to that existing name, recovered 13 of 13 (`rerun2\meta\recov_b_probeA.out`).
  - 7 are blocked by an explicit `Net-(R1-2)` label left by the repo's old auto label. H will not write an auto-looking name, so no H call recovers them.
  - probeC's 6 were not tried with (b).
- **12 were `touch` again:** geometry blocks both the stub and the label.

**Rule for the skill:**
1. After `[H:touch]`, retry with one fresh shared name.
2. On `[H:names]`, use the name the message reports.
3. Otherwise stop and report.

After any other code, stop and report. The unchecked repo label wrote wrong in 41 of 67 `names`, `sheet_pin` and `bus` cases.

**E's claim** ("a shared name always succeeds on free pins"):
- **Both pins free** (refused only as `blocked`): 14 of 15 recovered. The exception is DEC-23 `connect(#PWR02:1, R6:1)`, refused `[E:implicit]`: the power pin already names VCC, and E's message does not say so.
- **Pins not free:** the suggested fallback was refused on 521 of 527, including all 244 demo pairs.

## 8. E: "already connected", and E against H

| Sample | E refused as `[E:connected]` | M2 wrote cleanly on the same input | H wrote cleanly |
|---|---|---|---|
| Demo wire_pins_to_net / connect_pins | 449 / 241 | 447 / 84 | 447 / 84 |
| Probe A / B / C | 280 / 639 / 394 | 84 / 19 / 233 | 83 / 14 / 221 |
| llm-workflow / layouts and stock | 138 / 165 | 57 / 134 | 57 / 134 |

**What H's model buys over E:**
- 1,040 clean writes onto existing wiring where E refused;
- correct no-ops where E refuses: 509 of probeB's 639 `connected` refusals were correct no-ops for M2 and H, and H returns 514 no-ops on probeB against E's 0;
- 0 pins off their net with adaptive retries, against E's 99.

**What E gives up:**
- any edit that touches existing wiring;
- repeat connect_pins calls;
- stacked pins;
- pins with an implicit name (and it writes redundantly when that name equals N: a second VCC stub on DEC-23's power symbol).

Refusal rates show it: E 100% vs H 37.7% on demo `wire_pins_to_net`, 98.2% vs 3.6% on stock pins, 90.2% vs 23.7% on probeB. On fresh work the gap is small: 20.1% vs 17.3% on generated layouts, 32.1% vs 27.8% on hand designs.

**What E gains:** half H's code (742 vs 1,451 lines), no connectivity model to get wrong, and one refusal reason with one remedy in most cases. On these samples it is as safe as H; the difference is reach.

## 9. Code size

From `linecount.code_lines` (`rerun2\meta\linecount2.out`):

| File | Code lines | Physical lines |
|---|---|---|
| M2 | 1,278 | 1,669 |
| K | 1,416 | 1,923 |
| H (Hb is a 20-line loader) | 1,451 | 1,892 |
| E | 742 | 1,014 |

H is 173 lines larger than M2; the duplicate-reference and netclass rules outweigh the attribution code that was removed. E is 536 lines smaller.

## 10. Limits

- **GUI readings are emulated.** eeschema was never driven, and there is no KiCad 10 reading.
- **H's hierarchy scan cost:** about 8 s per call on vme-wren with the project present, against 0.8 s standalone (`h_build\perf_h.out`, builder, 10-02). The demo sweeps used standalone copies, so this is not in the rates. A product build needs a parse cache (inferred).
- **The 43 library-variant `dup_ref` refusals** might lift with a lib_id-plus-units comparison (inferred, unmeasured).
- **Not measured:** (b) on probeC's 6 cases, and Hb's one layouts recovery. Follow-up 1's skipped scenarios are listed in each batch's `not_rerun` notes.
- **Interruptions:** the 10-02 session end killed every batch. Its records are quarantined. Four demo tasks and one probeB shard hit kicad-cli startup crashes (exit 0xC0000142) and were re-run. Every call has a verdict except one probeB call (H16, a KiCad 10 local power symbol that kicad-cli 9 cannot load; every design wrote there unjudged). Corrected 10-03 after `dupref\gate.py` found it.
