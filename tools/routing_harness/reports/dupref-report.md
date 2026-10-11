# H-C1 with the pad-map test: measured (follow-up 3)

2026-10-03. The repo was not touched; every run used the read-only 54160c1 export. kicad-cli 9.0.8. Paths are under `%TEMP%\pr57pt\coarse\`. Tags: **measured** = kicad-cli; **emulated** = judge3's GUI-cleanup port; **inferred** = reasoning. Every figure comes from runs made on 10-03.

## 1. Result

- **The rule is built as specified:** `h2_impl.py` is H with only H-C1 replaced; **Hb2** = `hb2_impl.py` adds the crossing ban.
- **58 of the 63 calls where Hb's H-C1 fired lift:** 19 clean writes (delivered in both readings), 27 correct no-ops, and 12 refused for reasons the old refusal had masked (`touch` 9, `names` 3).
- **5 still refuse:** PI-15's 4 true duplicates, and 1 call refused by H-C1's scope, not by the pad-map test (section 3).
- **Hb2 passes the gate on every sample:** 0 wrong writes and 0 false no-ops in both readings. The one flagged no-op is DEC-23, the judge's blind spot for `#` nodes, measured correct in follow-up 2.
- **No real case contradicts the criterion.** Three synthetic cases do, all in one direction, and there KiCad is not a safe yardstick (section 4).

## 2. The rule as built

A shared reference is one part when all three conditions hold:
1. each symbol places a distinct unit, and no symbol's instance entries disagree on its unit;
2. every library definition resolves and is not derived;
3. the definitions agree on the pad map: for every pad number, the same units draw it, with the same electrical types.

- In the pad map, unit 0 means common to all units; a sub-symbol with no unit in its name counts as unit 0; body styles fold into their unit.
- Library name and bytes no longer matter. The refusal message keeps its shape and names the failed condition.
- Scope is unchanged: the whole hierarchy, every Reference and instance reference. Code: 1,483 lines against H's 1,451 (`dupref\linecount3.out`).

## 3. Every call where H-C1 fired

Sources: `dupref\lift.out` and `by_reference.out`. Hb2 is compared with Hb at the same call slot; each Hb2 write is judged in both readings.

| Shared reference | Calls (batches) | Hb2 outcome |
|---|---|---|
| IC14, vme-wren FPGA | 47 (probeB 26, probeC 12, layouts 6, demo 3) | 26 correct no-ops, 13 clean writes, 6 `touch`, 2 `names` |
| IC20, vme-wren clocks | 8 (demo) | 5 clean writes, 3 `touch` |
| W1 (demo) / U1, sonde xilinx (probeB) | 1 / 2 | 1 clean write / 1 correct no-op, 1 `names` |
| PI-15 true duplicates (R1, R?, C1, C?) | 4 (probeA) | still `dup_ref` (condition 1) |
| U4 on tinytapeout | 1 (probeC) | still `dup_ref` (condition 1) |

- **The tinytapeout refusal is a scope artifact.** An AP2112K whose Reference is U3 has a dead-path instance entry naming U4, so its unit 1 sits beside the 74CBTLV3257's unit 1. KiCad ignores dead paths. Counting only live instance paths would lift it (inferred, unmeasured).
- **The four lifted groups have identical pad maps** (`dupref\oracle.out`): IC14 has 20 symbols in 5 library-name variants with 784 pads each; U1 spans 4 library IDs. Each group has one Value and one Footprint.

## 4. Does KiCad agree?

- **The netlist never joins copies of a pad.** With every other symbol and all wiring removed (`dupref\isolate_test.out`), IC20's pads 4 and 8, PI-15's pads 1 and 2, and pad 3 of two different chips sharing U1 each stay in separate nets. The netlist holds one component per reference in every case, so it carries no part-identity judgement.
- **KiCad's only one-part notion is ERC `different_unit_net`.** It groups multi-unit symbols that share a reference: it fires for IC20 and all its variants, and for 74LS00 unit 1 plus 74LS04 unit 2 as U1, but never for PI-15's single-unit resistors. kicad-cli's ERC reports no duplicate references at all.
- **Pad map agrees, KiCad keeps the parts apart:** none found. PI-15 agrees on the map, KiCad does not group it, and condition 1 refuses it, so they agree.
- **Pad map disagrees, KiCad groups them as one part:** 3 synthetic cases, all refused by Hb2 (`isolate_test.out`, `disagree_test.out`):

| Case | Geometry | Netlist (isolated) | Hb2 refuses on |
|---|---|---|---|
| A | clocks IC20, pad 4 retyped `passive` in variant `_1` | 1 component, pads 4 and 8 apart | pad 4 |
| B | clocks IC20, pad 8 moved from unit 0 to unit 1 in `_1` | same | pad 8 |
| Mixed | 74LS00 unit 1 at (101.6, 101.6), 74LS04 unit 2 at (152.4, 101.6), both U1 | 1 component, pad 3 apart | pad 10 |

- **Mixed:** KiCad's grouping would put 74LS00 1Y and 74LS04 2A on one footprint pad, a short. Refusing is right; KiCad's grouping ignores what the parts are.
- **A and B:** the pad is physically the same, so requiring equal types and units is stricter than needed (inferred). That costs refusals only, never a wrong write. No real sample reached this branch.

## 5. Gate and refusal rates: Hb against Hb2

Hb is the hybrid report's column. Hb2 is from `dupref\gate.py rerun3 Hb2` (`gate_rerun3_Hb2.out`); the same script reproduces the Hb column exactly from the follow-up 2 records (`gate_rerun2_Hb.out`).

| Sample (first attempts) | Hb | Hb2 | Hb2 wrong (cli / gui) | Hb2 false no-ops |
|---|---|---|---|---|
| Probe A, 647 strict / Probe B, 796 | 28.3% / 24.4% | 28.3% / 21.0% | 0 / 0 | 0 |
| Probe C, 739 | 31.0% | 30.0% | 0 / 0 | 1 (DEC-23) |
| llm-workflow, 392 / generated layouts, 294 | 28.6% / 17.3% | 28.6% / 17.3% | 0 / 0 | 0 |
| Stock pins 112 / pairs 21 | 3.6% / 42.9% | 0.9% / 42.9% | 0 / 0 | 0 |
| Star 39 / junction fixtures 11 | 51.3% / 36.4% | 51.3% / 36.4% | 0 / 0 | 0 |
| Demo wire_pins_to_net 718 / connect_pins 244 | 37.7% / 67.2% | 36.9% / 67.2% | 0 / 0 | 0 |
| Fallback sample, 160 pairs | 3.1% | 1.2% | 0 / 0 | 0 |

- Pins left off their net are unchanged: skill policy 302 of 1,196 and 40 of 147; adaptive retries 0 and 0.
- Hb2 now matches M2 on stock pins (0.9%) and demo pins (36.9%).

## 6. Corrections and limits

- **Two errors found by `gate.py` and corrected in `hybrid-report.md`:**
  - probeC had counted 3 calls per design twice (a restarted queue ran one job twice at once). Its rates move 0.1 to 0.3 points; no verdict changes.
  - One probeB call (H16, a KiCad 10 local power symbol kicad-cli 9 cannot load) has no verdict in any design, Hb2 included.
- **GUI readings are emulated, and kicad-cli runs no annotation check.**
- **Not measured:** live-path scope (1 call), and a relaxed type or unit test (A, B).
