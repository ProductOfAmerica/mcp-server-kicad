# Pressure test: routing-proposal.md

Repo mcp-server-kicad, main at 54160c1. Oracle kicad-cli 9.0.8 (local). No KiCad 10 locally. Written 2026-10-01.

Tags:
- **measured**: kicad-cli 9.0.8 netlist, judged by node set.
- **emulated**: the KiCad 9 GUI's load-time cleanup, reimplemented from source and run through kicad-cli 9. No eeschema was driven.
- **sourced**: KiCad source, file:line.
- **inferred**: reasoning.

"P" is the proposal; "M" is my Phase 1 design (phase1-independent.md); "B" is the repo's current code.

## How this was tested

- **Phase 1** (before reading P): 11 research slices on KiCad's connectivity rules. An independent verifier rebuilt each slice's geometry and re-checked it. Digest: `%TEMP%\pr57pt\phase1-digest.txt`; raw: `phase1-research.json`.
- **Phase 2:** two reference implementations in `%TEMP%\pr57pt\proto\`:
  - `proposal_impl.py` is P as written, taking the reading most favourable to P wherever P is silent.
  - `mine_impl.py` is M.

  13 probe categories ran 215 cases on P, M and B. Each case compares kicad-cli netlists before and after, keyed by node set, and reports merges, splits, renames, class changes and pads listed in two nets. Each category had a verifier who rebuilt its cases and judged whether each finding follows from P's text (**rule**-level) or only from the prototype (**implementation**-level). Only rule-level findings count against P. Where verifiers disagreed on that, the finding is marked contested. Digest: `%TEMP%\pr57pt\phase2-digest.txt`; raw: `phase2-attack.json`. Case IDs (PI-01, JCT-P1, ...) index into it, and each case names its scripts.
- **Report critique:** four critics went over a first draft of this report (fact-check, design review, completeness, steelman for P), several with new measurements under `%TEMP%\pr57pt\critique-*`. Their corrections are folded in.
- **Not run:** codex-spec, an external spec review that could not run in this environment.

## 1. Verdict

**Adopt with changes.** The changes are mandatory and reach P's core mechanism. On top of them there is one design choice, where I recommend the stricter option.

**Keep from P:**
- the rule (a routing edit changes the netlist only by the requested join, or refuses with the file intact);
- the per-sheet model plus pre-write check architecture;
- the decision table. Refuse on a differently named net, proceed and report on an unnamed net, no-op when already on N, refuse on an NC flag, label on the pin as fallback: all five behave as stated (DEC-25, measured);
- no-op-first ordering;
- all-or-nothing calls with one write.

On fresh layouts built the way the repo's skills prescribe, P wrote no short, split or rename. B shorted 16 of 36 runs (LW-13, probe-measured, not re-run by a verifier).

**Mandatory changes**, each backed by measured failures of P as written:

1. **Pin geometry.** Replace the angle-only orientation fix with KiCad's rotate-then-mirror transform for positions and directions. As written, P wires the wrong pin in 4 of 12 orientations. Both designs need this prerequisite; P prescribes the wrong version of it.
2. **Junctions.** Delete routing auto-junctions. They split nets in kicad-cli 9, the reader behind this repo's netlist export, ERC and PCB update.
3. **Overlaps.** Ban collinear overlaps with any existing wire, the pin's own net included. They split nets in the GUI reading (emulated).
4. **Ports.** Treat sheet pins as names of unknown value. As written, rails merge through hierarchy ports.
5. **Pad identity.** As written, a multi-unit pad lands on two nets.
6. **Auto label.** Delete connect_pins' `Net-(ref-pin)` label. It makes later legitimate calls refuse, and it renames and splits port nets.
7. **Inert.** Delete the "inert" exception. It changes net classes, and its removal measured free for wire_pins_to_net.
8. **Two connectivity views.** Decide no-ops on joins every reader makes (a *certain* model) and refusals on joins any reader might make (a *possible* model). Build both with KiCad's capture, stacking, bus and bus-entry rules. As written, P reports false no-ops and lets stubs merge separate nets.
9. **Decoupling cap.** Compose it on one parsed tree with a single write, not snapshot-and-restore.
10. **Inputs.** Validate them, refuse no_connect-type pins, and resolve pin names to pads correctly.

**The design choice** is between P's allowed-set touch rule (contact with the pin's own net is allowed) and M's strict rule (new geometry touches nothing but the target pin's point). A critic built P with changes 2, 3 and 7 applied (PA) and compared it with M. They matched on every sample except dense existing wiring:

| Sample | PA | M |
|---|---|---|
| 294 generated-layout calls | identical | identical |
| 110 stock pins | identical | identical |
| 21 stock connect pairs | identical | identical |
| 20 star nodes | identical | identical |
| 11 junction fixtures | identical | identical |
| 244 dense-sheet connect_pins pairs accepted | 92 | 84 |

Neither wrote anything bad (critique-proposal-advocate, measured, one run). PA's 8 extra routes all run over own-net pin ends, which P's own line 33 calls a drawing that "looks connected". I recommend the strict rule. It buys back the same safety with no net-membership check in the geometry path, it keeps a defence against model bugs, and it costs those 8 routes plus a few stubs drawn as labels on the pin.

**M is not off the hook.** Its strict touch rule caused no merge, split or rename in any probe, but M as written still had defects:
- a rail short through a sheet pin lying on a wire interior (HIER-06, medium; 0 of 588 demo sheet pins lie on a wire interior);
- a merge with a global net through a sheet pin on the target's own wire (geometry verifier NEW-4);
- merges of duplicate-reference symbols (PI-15, low) and of a symbol with an unresolved lib_name (PI-17, low);
- false no-ops from deciding them on its over-approximate model, high where bus entries share a point (BUS-M2);
- it shares P's cross-sheet pad, duplicate-sheet-name and rule-area failures (DEC-14, HIER-09, INERT-05).

Section 5 fixes these. Several of the fixes are specified but not built (section 8).

## 2. Re-verified facts

| Claim | How checked | Result | Matches prior session? |
|---|---|---|---|
| S1-S4, S6-S9 shorts | `wptn_scenarios.py` rerun on 54160c1 (and again by the fact-check critic) | reproduce (measured) | yes |
| S5 foreign pin at stub end | the scenarios script places R3 one grid step off (no short); `wptn_orient.py` places it on-grid (short) | measured | yes, with that caveat |
| "Outward angle wrong in 6 of 12 orientations" | every pin of R, D and Q_NPN in all 12 orientations through both tools, netlist-checked; 1680 stock pins checked against a KiCad-order transform (sem-tolerance, PI-01) | **Two bugs, not one.** The angle is wrong in 2 orientations (rotation 90/270, no mirror). In 4 (rotation 90/270 with a mirror) the *position* is wrong: the repo mirrors before rotating, KiCad rotates first (sourced 9.0 sch_io_kicad_sexpr_parser.cpp:3071-3098, sch_symbol.cpp:1906-1913). The computed point is the true pin reflected through the origin, which is the other pin on R/C/D and empty space on a transistor. `wptn_orient.py` derived its "truth" from the same math. KiCad's demos hold 87 such symbols in 18 files (measured census) | **no** |
| Rotated R, 7.62 mm stub, self-short | `wptn_orient.py` | reproduces (measured) | yes |
| connect_pins L-route over a foreign label | `wptn_connect.py` | reproduces (measured) | yes |
| `_auto_junctions_cst` creates connections | sem-geometry, repro-extra, `main-checks/kept_junction_split.py` | yes, and it also **disconnects**: kicad-cli 9 truncates a wire at a junction on its interior, keeping the first point to the junction and dropping the rest (measured; sourced 9.0 schematic.cpp:1055-1072, 1147, where the far half is re-added only when a GUI "schematic holder" exists). The 9 GUI and KiCad 10 connect it (sourced) | partly new |
| Label on a pin end connects | sem-geometry G09 | yes, except on a no_connect-type pin (measured) | yes |
| Label anywhere along a wire connects | sem-geometry G10-G13, sem-tolerance | qualified (see note A below) | partly |
| Wire end or pin end on a wire interior does not connect | sem-geometry | yes (measured); one junction away from a real connection | yes |
| Crossing wires connect only with a junction | sem-geometry and verifiers | qualified: in kicad-cli 9 a junction on unsplit crossing wires joins only the two start halves. Only the pre-split form, where every wire ends at the junction, means the same in every reader (measured) | partly |
| Same-text local, global and hierarchical labels are one net | sem-naming, sem-hierarchy | yes on one sheet, and the join set is wider (see note B below) | yes, extended |
| power_in names its net if hidden or on a power symbol | 9.0 sch_pin.cpp:323-327; sem-symbols S9 | confirmed. A power symbol's net is named by its placed Value (only through a power_in pin, so PWR_FLAG names nothing); a hidden power_in pin by its own pin name (sourced and measured) | yes |
| Missing `(instances)` drops unconnected pins | `main-checks/instances_gotcha.py` | confirmed, and more general: any instance-path mismatch drops *all* unnamed nets, so a sub-sheet exported alone cannot see shorts between unnamed nets (measured) | yes, generalised |
| Collision logic from 38b466c | git history (code-survey H1) | confirmed (sourced). The fallback *is* exercised, but only by the two tests that assert success while KiCad merges the nets | partly: "untested" should read "tested only by tests that bless merges" |
| Two tests assert merges as success | code-survey T1 and T2 | confirmed. TestStubCollision's fixture makes the pins coincident (docstring 105.08, code 107.62). Two more tests encode bugs: `test_auto_direction_rotated_90` asserts the into-body stub, and `test_wire_pins_to_net_auto_junction` asserts the junction that joins a foreign wire | yes, plus two |
| P line 22, "items that share a point connect" | sem-geometry G01 verify | not quite: wire and bus never join directly, no_connect-type pins never join, and two bus entries never join each other (measured; sourced ConnectionPropagatesTo) | new |
| P lines 35-36, that test_t_junction_connect_pins "correctly joins" | JCT-P4 | correct in kicad-cli 9 only for the test's argument order; reversed it splits; without the junction it is disconnected in the GUI reading (measured, emulated) | new |
| P line 87, that the PWR_FLAG helper scans every unit | schematic.py:1430-1441 (completeness critic) | holds | new |

**Note A (label capture).** Capture is `TestSegmentHit` with accuracy 1: |d| ≤ 1 IU (0.0001 mm) off an orthogonal wire's interior, and KiROUND(d²) < 4 IU² at ends and along diagonals. In 9.x and 10.0.0-10.0.5, a pin end, NC flag, other label or sheet pin at the anchor stops the attach; from 10.0.6 only a pin end or NC flag does (sourced). A label with two or more lines under it joins all of them, junction or not, and 2-point graphic polylines count as lines. Sheet pins attach like labels but only to the first line hit (measured; sourced).

**Note B (same-text joins).** Besides the three label kinds, power Values and hidden power_in pin names join by text, and every name on a subgraph joins, transitively, not only the elected one. A local label does not reach a same-named item that exists only on another sheet, with three exceptions (measured):
- through a same-named global or power item on its own sheet;
- through the member names of a bus sheet pin or bus label (BUS-B1);
- across sibling sheets that share a name (HIER-09).

Other facts that shape the fix (measured unless tagged):

- **Unit.** KiCad takes a symbol's unit from the `(instances)` entry matching this sheet path, else the first entry, else the top-level `(unit)` (sourced 9.0 sch_symbol.cpp:727-733, 865-878). The repo reads the top-level `(unit)`.
- **Library symbol.** KiCad resolves by `lib_name` when present, else by `lib_id`, exact match and no fallback; an unresolved `lib_name` stacks every pin at the symbol origin (sourced 9.0 sch_symbol.cpp:242-248, 346-347; measured PI-17). Of 219 lib_name symbols in the demos, the repo resolves 171 to a different entry (identical pins) and 37 to nothing.
- **Pin lookup.** "Name or number, first in file" resolves a number to a different pin by name in 25 stock symbols. 1,927 of 9,644 non-derived single-unit symbols share a power or ground name across pads at different points (PI-14, LW-04).
- **Collinear overlaps.** kicad-cli 9 does not join overlapping wires; the GUI merges same-stroke overlaps on load (sourced; emulated).
- **ERC is no safety net.** It misses label-made and cross-sheet shorts, drops markers with equal |p|² (sourced 9.0 erc_settings.cpp:323-334), and reports positions in mm/100. `run_erc` on a sub-sheet returns 0 unless the sheet's name equals its file name.
- **Derived symbols.** `place_component` embeds them (`extends`) unflattened. That covers 54% of stock symbols, and the file then fails to load. The hand-picked list of parts an LLM would likely choose is mostly derived (inferred, not from a live LLM).
- **No API.** No KiCad 9 or 10 API exposes schematic connectivity: the IPC schematic handler registers only GetOpenDocuments and needs a GUI (sourced). KiCad 11 (unreleased) adds GetSchematicNetlist and a headless api-server (sourced). KiCad master switched to a new connectivity engine on 2026-09-16 (sourced).
- **Behaviour changes inside 10.0.x.** The label stacking rule changed in 10.0.6. This repo's Windows KiCad 10 gate pins 10.0.5 (`.github/workflows/windows.yml:50`), while the macOS gate is unpinned.

## 3. Problems in the proposal, most severe first

Each problem lists the proposal text it rests on, the evidence and the fix.

**P1. The prescribed orientation fix wires the wrong pin (high).**
- *Text:* lines 50-51 change only the outward angle ("wrong for 90 and 270 degree rotations, mirrored or not"). Positions stay mirror-before-rotate.
- *Evidence:*
  - P is correct on 56 of 84 calls per tool. All 28 failures are rotation 90/270 with a mirror: R and D put the other pin on N, Q_NPN puts nothing on N, and every message is a plain success (PI-01, measured and re-measured; my emulation `main-checks/angle_only_fix.py` agrees).
  - On KiCad's own demo sheets, P is wrong on 35 of 47 calls (probe count; the verifier sampled 18 calls, all matching), and calls that should be no-ops merge nets (PI-02).
  - vme-wren/clocks C116:2 gets "Wired 1 pin" while KiCad keeps it off the net (LW-03).
  - In the 4 mirrored cases the repo's angle was already right, so P's flip breaks them once positions are fixed (PI-12).
- *Mitigation in P:* its 12-orientation tests would catch this if they assert which pin KiCad puts on N. Its "geometrically otherwise" fallback would pass.
- *Fix:* one integer-IU transform in KiCad's order, used for both position and outward direction (M 5.1; 84 of 84 measured).

**P2. Kept automatic junctions split nets in kicad-cli 9 (high).**
- *Text:* line 56 "Keep automatic junctions"; lines 30-31 let a stub meet the pin's own net or N.
- *Mechanism:* when a stub end or L corner rests on the interior of an own-net or N-net wire, the kept helper writes a junction without splitting the wire. kicad-cli 9 then drops the far part whenever the hit wire starts on the kept side, which is about half of drawing directions; the counts below already include that condition.
- *Measured* (JCT-P1, JCT-P2, LBL-02, DEC-17, CMP-03, perf-04, LW-01, main-checks):
  - R1:1 already wired outward to R2:2: after `wire_pins_to_net(R1:1, N)`, R2:2 is unconnected.
  - Caps under a VCC rail: R11:1 drops off VCC, and this repo's `update_pcb_from_schematic` then binds R11 pad 1 to `unconnected-(R11-Pad1)` with `status="ok"`.
  - Stock pins: 22 of 60 P outputs split their own net (verifier: 33 of 110), plus 5 of 17 connect_pins pairs and 4 of 26 star-node placements.
- *Other readers:* the 9 GUI and KiCad 10 read these files as connected (sourced, emulated). On KiCad 9 hosts the split is real for this repo.
- *Fix:* routing writes no junctions. A stub-end label already joins any line it lands on in every reader (sem-geometry G11). With the junctions gone and overlaps banned (P3), the same stock sample comes out clean (critique PA, measured).

**P3. Own-net contact admits collinear overlaps, which split nets in the GUI reading (high consequence; emulated).**
- *Text:* lines 30-34 make any contact with "the nets being joined" safe.
- *Effect, through the GUI-cleanup emulation (perf-03, JCT-P4, LW-01; mechanism sourced 9.0.8 sch_line.cpp:551-557, files-io.cpp:539, sch_edit_frame.cpp:152):* on stock pins whose wire leaves outward, P's stub runs along that wire past another pin or corner. The GUI merges the overlap on load, and the far pin ends up mid-wire, off the net.
- *Counts:*
  - 5 of the 38 kicad-cli-clean P outputs among 60 stock pins (verifier: 12 of 110);
  - 7 of 17 accepted connect_pins pairs, 5 of them clean in kicad-cli 9;
  - 13 of 26 star-node placements.
- *P's own justification:* the D3 test it cites for own-net contact (lines 35-36) is right in kicad-cli 9 only by argument order.
- *Save:* opening the file in the editor and saving persists the merged wire (inferred from source).
- *Fix:* any collinear overlap with an existing wire or bus blocks, own net included, with the label on the pin as the fallback.

**P4. Sheet pins are not names, so rails merge through ports (high consequence; uncommon trigger).**
- *Text:* lines 27-29 list label kinds and power pins as the only name sources; line 31 lists sheet pins apart from names. A net reached only through a sheet pin is therefore "unnamed", and line 41 proceeds.
- *Evidence* (HIER-01/02/03, DEC-03, NAM-02, synthetic fixtures, measured): the child side of the port carries +5V, a global name or a child-local name.
  - `wire_pins_to_net(R1:1, 'GND')` merges +5V into GND.
  - `connect_pins` between two ports shorts +5V to GND.
  - Inside a two-instance child, one call merges both instances' nets into +5V.
  - ERC reports nothing new.
- *Trigger:* a parent net wired straight to a port with no parent-side label, whose child side carries a strong name. KiCad's demos have 6 port-only parent nets (10 of 1,739 parent-sheet pins) and none with a strong child-side name (measured). The repo's own add_hierarchical_sheet writes a parent label, which makes P refuse. Frequency in other designs is unmeasured.
- *Fix:* a sheet pin on the target's possible component is a name of unknown value, so refuse unless the certain component already carries N. Index sheet pins with label-style capture (HIER-06).

**P5. No pad identity, so a multi-unit pad ends up on two nets (high).**
- *Text:* P has no pad-identity rule; a pin is one drawn point.
- *Evidence* (UP-01/02/03, DEC-11/12, measured):
  - Wiring a unit-0 (common) pin leaves its other copy unconnected. kicadxml lists the pad in two nets, and the repo's PCB import binds the last-sorting name, `unconnected-(...)`, with `status="ok"`.
  - If the other copy is already on X, P wires its copy anyway, and the pad sits on X and N.
  - P reports "already on N" when only one copy is on N, and no P call can finish the pad.
- *Scale:* 138 non-derived stock symbols have unit-0 pins (94 of them a visible power_in), and 11 demo sheets place two or more units of such parts.
- *Fix:* a pad is (reference, number) across the placed units of one symbol. Refuse on any copy's foreign name, wire every copy, and decide the no-op per copy.

**P6. Pin names resolve to the first pad only (medium; level contested).**
- *Text:* P is silent on resolving a name to pads. The prototype kept the repo's first-match lookup, so verifiers rated this unclear rather than rule-level.
- *Evidence:* this was the only P defect that fired on fresh LLM-style layouts. 17 calls wired an ambiguous name only partially, leaving 22 planned pins floating (LW-04, PI-14, measured).
- *Fix:* an exact number wins. A name matching pads drawn at one point is one target. A name matching pads at distinct points is refused with the list.

**P7. connect_pins' automatic `Net-(ref-pin)` label (medium).**
- *Text:* line 66 keeps it.
- *Evidence* (NAM-01, LW-02, DEC-01, JCT-P8, measured):
  - `connect_pins(R1:1, R2:1)` followed by `wire_pins_to_net([R1:1], 'VCC')` is refused as "already on net Net-(R1-1)". So are batches, and so is joining two such nets. The caller learns the net is named only from that refusal. `main-checks/autolabel_refusal.py` shows that the repo writes the label; the refusal comes from the NAM-01 runs.
  - The label also renames a net named only through a sheet pin (NAM-03), splits a promoted sheet-pin join (NAM-04; mechanism sourced 9.0.8 connection_graph.cpp:1874-1919, 2057), and joins by name a stale net carrying the same text (NAM-10).
- *Why exempting the name does not work:* KiCad elects the alphabetically first of equal-priority labels, so `/Net-(R1-1)` beats `/VCC` (measured).
- *Why the label was added:* to avoid wire_dangling ERC errors in hierarchical sheets (commit 465ae6b). kicad-cli 9 ERC showed no such error without it (measured on one fixture, critique). get_net_connections, which is seeded only from label text, also leans on it.
- *Fix:* delete the label, and ship a pin-seeded get_net_connections in the same slice.

**P8. The model's rules produce false no-ops and merges (medium).**
- *Text:* line 24 ("a label or a junction anywhere along a wire joins that wire") has no stacking exception; line 21 joins bus-entry ends.
- *Evidence* (measured):
  - False "already on N" when a pin end, NC flag or second label sits on the label's anchor, which KiCad 9 detaches (LBL-04, NAM-05, DEC-06).
  - False no-ops and "already connected" at 12 of 12 demo bus points shared by two entries (BUS-P1). This holds under the reading that an entry conducts end to end; with unlinked entry ends, a literal P refused correctly at the 5 points tested.
  - A stub allowed onto a wire P wrongly thinks is on N, merging a separate net (LBL-03, BUS-P2).
- *Contested attribution:* KiCad captures a label 1 IU from new geometry that an exact touch test misses (GM-01..06). The geometry verifier rated this rule-level; the label, inert and naming verifiers rated it implementation-level or unclear, because P promises a touch "wider than KiCad's rules".
- *Fix:* the certain/possible split of section 5.2, built with KiCad's capture, stacking, bus and bus-entry filters (sch_bus_entry.cpp ~572-602, sch_line.cpp:707-717).

**P9. "Inert" is too loose (medium only for directive labels).**
- *Text:* line 31.
- *Evidence* (measured):
  - A netclass directive on a dangling wire moves the requested net, and every pin already on it, into that class (INERT-01, medium; also visible in kicadsexpr).
  - The rest are low, and several need a later GUI drag or a non-KiCad writer: a lone junction on the new wire truncates it in kicad-cli 9 (INERT-04); an inert wire collinear with a foreign wire is merged by the GUI (INERT-07, emulated); a bus-entry end on it joins in kicad-cli 9 (INERT-08); latent T contacts become real on a later drag (INERT-02/03, sourced and emulated).
- *Cost of deleting it:* nothing measurable for wire_pins_to_net. connect_pins refuses aligned pins that have an object on the straight route (INERT-14), and the fallback recovers those. In every sample the critic ran, the inert clause never fired once the other changes were in.
- *Fix:* delete it.

**P10. auto_place_decoupling_cap: snapshot and restore (medium).**
- *Text:* "It snapshots the files it writes and restores them exactly if wiring fails."
- *Evidence* (CMP-01, CMP-07, measured):
  - A killed process, or a file held by another program, leaves a placed, unwired cap. P's 4 writes span about 1.9 to 3.1 s on one 1.17 MB demo sheet (load-sensitive).
  - A held file makes the restore fail too, leaving the refused state on disk.
  - The restore silently discards a concurrent edit to the root.
- *Hazards it inherits:*
  - the cap's own placement joins existing pins to VCC/GND (CMP-02, UP-07);
  - the kept junction splits a rail (CMP-03);
  - the hard-coded up/down puts labels inside the body at rotation 180 (CMP-04).

  Both designs also land the cap on sheet-local `/kid/VCC` on a sub-sheet (CMP-05, shared).
- *Fix:* compose placement and both wirings on one parsed tree and write once (section 5.6).

**P11. no_connect-type pins (medium).** P wires a pin of electrical type no_connect and reports success, but KiCad never connects it (DEC-09, PI-10, measured; sourced 9.0.8 sch_pin.cpp:1791-1794). *Fix:* refuse such targets, using the effective type including a placed alternate.

**P12. No input validation (low to medium).**
- `label_text=''` joins unrelated calls into `<NO NET>` (NAM-07).
- A KiCad auto name (`Net-(R3-Pad1)`) or a printed form (`/SIG`, `SIG `) creates a look-alike net (NAM-08; naming verifier NEW-2).
- Bus syntax writes a bus label on a wire (NAM-09).
- A zero or negative `stub_length` is accepted (GM-14).
- A non-grid `stub_length` (2.5401) produces a 1 IU near miss that merges (GM-03).

*Fix:* section 5.4 step 1.

**P13. Smaller text defects.**
- "A plain crossing of another wire" should say "wire or bus". Without that, P refuses routes that merely cross a bus (BUS-P6; the verifier rated it medium; one-word fix).
- The stated reason for refusing the label-on-pin fallback is wrong for a single wire. KiCad's pin end stops the attach, so the real hazard is a second line (DEC-18, LBL-08; sourced 9.0.8 sch_label.cpp:1097-1113). The refusal itself is defensible.
- The invariant (lines 8-13) has no delivery clause, so a call that never puts the pin on N satisfies it (units-pads NF5).
- 2x and 3x stubs help only when a line crosses the 1x end (GM-17). Harmless.

**P14. Performance as built (implementation-level).**
- *Measured:* P's prototype rebuilds its model per pin and takes 7.0 to 13.3 s for 50 pins on the largest stock sheets. B takes 1.8 to 2.4 s and M's prototype 1.1 to 2.6 s.
- A build-once variant of P's rules took 2.1 to 2.5 s with identical outputs (perf-01).
- P's text does not require a per-pin rebuild. Build once.

**Two figures read with care:**
- *27 of 60 / 45 of 110.* These were the headline in the first draft. They measure only two rules. Of the verifier's 45 bad writes in 110 stock pins, 33 are kicad-cli 9 truncations from the kept junctions (P2) and 12 are GUI-reading overlap merges (P3). The sample excludes mirrored, multi-copy, NC-type and power_in pins.
- *Refusal rates.* On 36 generated layouts P refused 16 of 294 first-attempt calls and M 51 of 294 (probe counts). The gap is entirely pin-name resolution; the two touch rules agreed on every call there (critique, measured).

### 3b. Problems the same probes found in M as written

The fix for each is in section 5. "Measured fix" means a variant was built and run; otherwise the fix is specified only.

**Merges and shorts:**
- *Sheet pin on a wire interior not attached* (HIER-06, medium). M merged +5V into GND. A related case, a sheet pin on the target's own wire interior, merged N into a global net (geometry verifier NEW-4). *Fix specified:* index sheet pins with label capture.
- *Duplicate references treated as copies of one pad* (PI-15, low; place_component never creates them). *Fix specified:* only distinct units of one library symbol are copies; otherwise refuse and ask for annotation.
- *Unresolved lib_name modelled at lib_id positions* (PI-17, low). KiCad stacks every pin at the origin. *Fix specified:* resolve exactly as KiCad does, and refuse an edit that touches the origin.
- *Shared with P:* pad copies on other sheets (DEC-14, HIER-14, UP-05, medium), duplicate sibling sheet names (HIER-09, low), rule-area class changes (INERT-05, medium).

**False no-ops and contradictions:**
- *No-op decided on the possible model* (JCT-M1 medium, BUS-M2 high, DEC-07). Section 5.2 of M said that model decides only refusals, yet 5.4 and 5.5 decided no-ops on it. The result is false no-ops on version-dependent geometry and also on version-independent bus geometry (12 of 12 demo shared entry points). *Measured fix:* the prototype's separate certain model.
- *Half-wired pad reported as a no-op* (DEC-13, low), from taking the union of the copies' names. *Measured fix:* decide the no-op per copy.
- *"New local net" reported when N joins a bus member on another sheet* (BUS-B1, low). *Fix specified:* expand bus members.

**Refusals of legitimate edits:**
- *Bus members lumped into one component* (BUS-M1, low after verification). *Fix specified:* separate bus subgraphs.
- *Port rule pre-empting "already on N"* (HIER-07, DEC-05, LW-10). This came from the correction applied to M during the probes; M's original text decided the no-op first. *Measured fix:* no-op first.
- *Port-only nets refused* (DEC-04, HIER-08: 10 of 1,739 demo parent-sheet pins). Safe; a policy choice (section 7).
- *A pin name on stacked pads refused* (LW-07, PI-19). 70% of non-derived MCU symbols have one, and this was M's largest refusal source on generated layouts. *Fix specified:* one target when every matching pad sits at one point.
- *0.05 mm tolerance* refusing near misses and any-angle lattice wires that are clean in every reader (GM-11, LBL-10). *Fix specified:* KiCad's predicate plus a few IU.
- *A label refused on a pin end that carries KiCad's pre-split junction* (JCT-M3), *an NC-type pin end at the target* (UP-11), and *a 2-point graphic line over a pin end* (INERT-13). *Fix specified:* section 5.3 rule 1.
- *connect_pins refusals:* 28% on generated layouts, the same as P (LW-09), and 66% on dense demo sheets (builder sweep). The suggested wire_pins_to_net fallback recovered every generated-layout refusal and 158 of 160 dense-sheet ones; the other 2 hit the JCT-M3 junction rule (critique, measured). The fallback names the net. The D3 tee is refused (JCT-M2). *Fix specified:* 3-segment routes, which were measured to exist and be clean for every refused generated pair.

**My Phase 1 performance figures** came from the perf slice's prototype, which implemented a P-like rule rather than M. `mine_impl` is 0.9 to 1.6x faster than B (perf-06).

## 4. Alternatives considered

| Option | Verdict | Where it wins | Where it loses |
|---|---|---|---|
| P's allowed-set touch rule as written (own net, N, inert) | rejected | fewer label-on-pin fallbacks | as written it carries P2, P3 and P9 |
| Allowed set grounded on the certain model, plus an overlap ban and no junctions (PA) | close second | 8 more of 244 dense-sheet connect routes; a few more stubs instead of labels on the pin (measured) | needs net-membership checks in the geometry path; its extra routes run over own-net pin ends ("looks connected"); depends on the certain model being sound (it passed 75 demo sheets, measured); does not rescue D3 or star nodes (critique, measured) |
| Strict touch rule (M) | **recommended** | geometry check needs no net membership; output sits in the geometry every reader agrees on; the strict rule itself caused no merge, split or rename in any probe | label on the pin is common on existing wiring (369 of 453 demo writes; 269 because a wire blocked the stub); connect_pins needs more route shapes |
| Label on the pin as the default | not adopted | one contact point | changes today's look and the tests; eeschema drag behaviour with a label on a pin is unmeasured (inferred risk) |
| Runtime kicad-cli before/after gate | rejected as the mechanism; kept as the test oracle | exact for the installed version | unavailable without KiCad; 9 cannot read 10 files; 0.4 s per small sheet and 6 to 11 s on vme-wren (measured); a sub-sheet export omits unnamed nets; kicad-cli 9 is not GUI-faithful (truncation, overlap) |
| Probe trick (test points on wire ends, one export) | rejected | item-to-net map in one call (0.39 s, measured) | same limits as the gate |
| KiCad IPC API | unavailable | KiCad 11's GetSchematicNetlist would answer this exactly (sourced) | no schematic support in 9 or 10; GUI only |
| Full Python connection graph with hierarchy and name election | partly | should back get_net_connections, which is wrong today | name election and cross-sheet naming are order- and version-dependent (sem-naming N04 verifier), and refusing correctly does not need them |
| Post-write ERC | rejected | free | misses most shorts; drops markers (measured) |
| Snapshot/restore for composites (P) | rejected | small change | partial-write windows; discards concurrent edits (P10) |
| Pre-split tee for connect_pins (split the target wire, junction where all lines end) | optional, later | clean in every reader (measured on D3) | edits an existing node; needs the user's ruling (section 7, Q4) and a node-level byte-preservation check |
| P's 2x/3x stubs | optional graft | nicer drawing when a line crosses the 1x end | helps only that case |

## 5. Recommended solution

### 5.1 Contract

- **Delivery.** After success, in kicad-cli 9, the KiCad 9 GUI and KiCad 10, every requested pad is on N (every placed copy, on every sheet where one is drawn), or the two pins are connected.
- **Nothing else changes:**
  - no other nets merge;
  - no net splits;
  - no pad ends up listed in more than one net unless it already was;
  - no existing net is renamed (only the requested pins' nets take N);
  - the joined net's class set becomes the union of the joined nets' sets plus any classes the project's netclass patterns give N, and every other net's class set is unchanged.
- **Atomic.** One write per file. A refusal leaves every file byte-identical and lists, per pin, the outcome, the obstacle and a remedy, including the pins that would have succeeded.

### 5.2 Shared module `_connectivity.py` (stdlib, over the CST, built once per call)

**Coordinates and items.** Coordinates are integer IU, `KiROUND(mm × 10000)`, compared exactly. The items are:
- wire and bus segments;
- 2-point graphic polylines, which count only for label rules;
- bus entries, both ends symmetric;
- junctions and NC flags;
- labels of four kinds, directive labels included;
- placed pins;
- sheet pins;
- rule areas with their directive classes;
- the project's netclass patterns.

**Placed pins:**
- KiCad's transform: rotate, then mirror. A `(mirror)` written before `(at)` is ignored, as KiCad does (PI-13).
- Body style from `(body_style)` or `(convert)`: De Morgan handling was correct in both designs (UP-16).
- Hidden pins included.
- Effective electrical type, including a placed alternate.
- The unit is computed per instance path. A symbol whose instances of this file draw different units refuses, except a unit-0 pad drawn at one point by every instance when N is a power or global name present on this sheet (UP-10).
- The library symbol is resolved exactly as KiCad does: `lib_name` if present, else `lib_id`, exact match, no fallback. An unresolved `lib_name` places every pin at the symbol origin. Any derived or unenumerable symbol anywhere on the sheet refuses the whole call.

**Certain model C** (joins every reader makes):
- Exact point joins under KiCad's propagation filters:
  - wire and bus never join directly;
  - no_connect-type pins never join;
  - two bus entries never join each other;
  - an entry joins nets only at its non-bus end;
  - a bus joins pins, labels and sheet pins at exact points, plus labels anchored on it.
- A label attaches to the line under it (Note A capture) only when nothing at the anchor blocks it in any version.
- A sheet pin attaches only when exactly one line is within capture.
- Two or more lines under a label are joined.
- A wire that truly overlaps another stroke-equivalent line is *uncertain*: C uses none of its joins.
- For a junction or entry end on an unsplit wire, only the half kicad-cli 9 keeps (first point to the nearest cut).

**Possible model P0:** C plus every version-dependent join:
- unsplit-junction pass-through;
- collinear overlaps;
- label stacking under 10.0.6+;
- bus-entry ends on wire interiors;
- junctions on bus points;
- KiCad 10 jumper groups (sourced).

**Names:**
- *Strong names* are the text of the three label kinds, plus names from power_in pins only. On a power symbol the name is the placed Value (PWR_FLAG names nothing). A hidden power_in pin on any other symbol is named by its library pin's primary name. A `(power local)` name is sheet-scoped and never global.
- Text variables resolve from project variables and built-ins (`${#}`, `${SHEETNAME}` per instance, ...). Text that does not resolve to the same value in every instance is an *unknown name*.
- `/` and `{slash}` normalise to one form.
- Same-text names close transitively on the sheet.
- Bus-syntax sheet pins and bus labels expand into member names.
- Sheet pins are *ports*: names of unknown value.

**Two reading rules:**
- Every no-op and "already connected" test reads **C**.
- Every refusal test reads **P0**.

**Cross-sheet facts** come from a raw-bytes scan of the project's sheets, not a CST parse: 57 ms against 7.2 s for vme-wren (critique, measured), cacheable per file on mtime and size. The facts are:
- global and power names;
- per-instance references and units;
- copies of a pad on other sheets;
- duplicate sibling sheet names.

### 5.3 Touch rule (geometry)

Let P be the target pin's connection point.

1. **At P.** New items may coincide only with items C joins to the pin (other pin ends, wire ends, a junction where every line ends) and with no_connect-type pin ends, which KiCad never joins. Refuse if:
   - P holds an NC flag or a bus or bus-entry end;
   - a line passes through P without ending there;
   - a label or sheet pin captures any new segment, end caps at P included.
2. **Every other new endpoint** (stub end, route corner): no item at the point, no line within capture, and no label or sheet pin capturing it.
3. **Every new wire interior:**
   - passes through no pin end (the target's own other pins included), wire or bus end, junction, NC flag, bus-entry end or sheet pin;
   - is captured by no label or sheet pin;
   - overlaps no wire or bus collinearly, own net included.

   Crossing a line's interior at a clean point is allowed.
4. **The new label** sits on no existing label, NC flag, sheet pin or bus-entry end. No line captures it except its own stub, or, for a label on the pin, wires that end exactly at P.
5. **Class sources.** New geometry adds no class source (directive label or rule area) outside the joined net's allowed class set (5.1).
6. **Junctions.** Never write a junction on a line interior. A dot is allowed only where every line ends at the point, which matters only if the optional tee is adopted.

Capture uses KiCad's predicate plus a margin of a few IU. There is no visual tolerance. `stub_length` must be a positive multiple of 1.27 mm, which also removes the tool's own near misses (GM-03).

### 5.4 wire_pins_to_net

1. **Validate.**
   - `label_text`: non-empty and trimmed; no leading `/`; no bus syntax; not `^(Net|unconnected)-\(`; no `${`.
   - `direction`: the closed set.
   - `stub_length`: as in 5.3.
   - Deduplicate the pins.
2. **Resolve pins to pads.**
   - An exact number wins. If the same string is also another pad's name, refuse and list both.
   - A name matching pads drawn at one point is one target; pads at distinct points are refused with the numbers.
   - A no_connect-type target is refused (KiCad never connects it).
   - An NC flag on any copy is refused, suggesting remove_no_connect, which must then act on every copy (5.7).
3. **Copies on other sheets.** Proceed only if every copy drawn on another sheet is already on N in C, read from its own sheet. Otherwise refuse, naming each copy's sheet, unit and the call to make there. If the hierarchy cannot be read, refuse.
4. **Decide per pad, in order:**
   1. every copy's C-component carries N: **no-op**;
   2. any copy's P0-component carries a strong name not C-joined to N, an unknown name, or a port: **refuse**, naming it. Phase 2 of port handling (later) follows the port into the child file instead and allows chains that carry only hierarchy-local names;
   3. otherwise proceed. If only P0 puts the pin on N, a label on the pin makes every reader agree.
5. **Geometry per copy.** Try the outward stub, optionally a 2x stub when a line crosses the 1x end, then a label on the pin. Add the chosen items to the index before the next pin.
6. **All or nothing.** One write.
7. **Report:**
   - each pin's outcome and its C net-mates;
   - whether N joined existing items on this sheet, and of which kind (label, power, global, bus member, or a same-named sheet pin that a local label does *not* reach), or is a new local net;
   - the instance paths the edit applies to;
   - duplicate sibling sheet names, if any.

   When N is a power or global name elsewhere in the project but absent from this sheet, follow the user's ruling (section 7, Q2).

### 5.5 connect_pins

**Decisions, in order:**
1. Same C-component, or C name sets intersect after C closure: **no-op**.
2. Both P0 sides carry names (ports and unknown names count) and share no C-certain name: **refuse**, naming both.
3. A port or unknown name on one side refuses only if the other side carries any name.
4. Otherwise route, including the case where only P0 connects them, since a clean route makes every reader agree.
5. Multi-copy pads: refuse, suggesting wire_pins_to_net.
6. no_connect-type pins: refuse, saying KiCad never connects them.

**Routes:** straight, then horizontal-first L, vertical-first L, then 3-segment routes on the 1.27 mm grid, ranked by length. The touch rule applies at both ends, at corners and along legs. No label, no junction, one write. If every route is blocked, refuse and suggest wire_pins_to_net with a shared name.

### 5.6 auto_place_decoupling_cap

- Compose placement and both wirings (direction auto) on one parsed tree.
- **Placed pin ends follow rule 2.** The one exception: exact coincidence with a pin end, a wire or bus end, or a junction where every line ends, whose C-component carries exactly the requested name, counts as a no-op for that pin. A placed pin end may never sit on a label or sheet-pin anchor or on a line interior. A cap pin on a mid-wire label splits the rail (critique e1, measured).
- **Backstop:** after placement, rebuild C and P0 for the pre-existing items and refuse if any pre-existing component changed.
- **Instance entries:** write one per instance path, with distinct references (HIER-15).
- **Root `symbol_instances` entry:** KiCad ignores it for files at 20221002 or later (sourced 9.0.8 eeschema_helpers.cpp:180), but this repo's `get_symbol_instances` reads it. Either write it best-effort after the sheet and report any failure, or drop it in every tool and update `get_symbol_instances`.
- **Write the sheet once**, and apply the same N-scope check as 5.4.

### 5.7 Scope and slicing

The model and the touch rule are shared code from the first routing slice. Ordering constraints:
- Fixing orientation alone makes today's tool split nets on unmirrored 90/270 parts. Its stubs then run along existing outward wires, and the kept junction truncates them (critique e3, measured). So the transform fix ships together with deleting routing junctions and the overlap ban.
- Deleting the auto label leaves get_net_connections unable to find nets made by connect_pins. So a pin-seeded get_net_connections ships in the same slice.
- On derived symbols, which cover most MCUs, routing cannot run at all until place_component flattens them.

| Slice | Content |
|---|---|
| 0 | place_component flattens derived symbols, or refuses them before writing. |
| 1 | Pin geometry and resolution; `_connectivity.py`; wire_pins_to_net. remove_no_connect and no_connect_pin act on every copy of a pad, and no_connect_pin refuses a connected pin or a label anchor (G14, F08). |
| 2 | connect_pins, plus get_net_connections on the model with a pin-seeded mode. |
| 3 | auto_place_decoupling_cap and add_power_symbol as one-write compositions. |
| 4 | place_component and move_component guards; add_wires and add_junctions write pre-split junctions (pending Q4); add_hierarchical_sheet and add_sheet_pin collision checks (W10, W11). |
| 5 | Port following. |

Separate issues found on the way:
- `run_erc`'s sub-sheet filter;
- ERC positions in mm/100;
- the auto PWR_FLAG. **Delete it** from wire_pins_to_net: it crashes kicad-cli ERC through a lib_id/lib_name mismatch (RX-13, LW-06), is decided per call rather than per net (RX-15), and M never placed one and stayed clean. This changes the `auto_pwr_flag` parameter and the verification skill's promise;
- add_hierarchical_sheet accepting duplicate sheet names (HIER-09);
- flatten_hierarchy and duplicate_sheet merging nets.

**connect_pins between two differently named nets:** both designs refuse, no probe found a legitimate merge through it, and the low-level tools remain for deliberate merges. Refuse, with no override flag.

### 5.8 Feasibility

- **Performance.** Build once per call; never re-walk the CST per pin. M's prototype handled 50 pins in 1.1 to 2.6 s on the largest stock sheets, against 1.8 to 2.4 s for B (measured, shared machine). Cross-sheet facts cost about 57 ms on vme-wren through a raw scan. Add a vme-wren budget test for a sub-sheet call.
- **Drift risk.**
  - The touch rule keeps output in geometry every reader agrees on, so drift does not make the *writes* ambiguous.
  - The decisions are exposed. If a reader stops making a join that C assumes, the tool reports a false no-op. If a reader starts making a join that P0 lacks (10.0.6's stacking change was one), a refusal is missed.
  - Hence the model-versus-KiCad differential in section 6 must run as a gate on every KiCad version in CI. Version-dependent joins belong in P0, never in C.
  - KiCad master's new connectivity engine (sourced) is the next drift point.

## 6. Test plan changes

- **Oracle helper.** Key nets by code or node set, never by name: kicadxml prints unescaped names, and two nets can share one (measured). Report merges, splits, renames, class changes and pads listed in two nets. `conftest.netlist_nodes` collapses same-name nets; add a coded variant.
- **Byte preservation on every successful routing write.** A node-level "only these nodes were added" diff. The repo's single-span helpers cannot express wire splits.
- **Output validity.** Add `direction`, `stub_length` and `label_text` of both routing tools to `test_output_validity.py`. Append the write-path change to the status log of `docs/adr-cst-substrate.md`.
- **Structural lint on every routing output**, independent of any reader:
  - no junction strictly inside a wire;
  - no collinear overlap;
  - no new endpoint on a line interior;
  - no label captured by a foreign line;
  - one write per file.
- **Fixtures.** Pre-split, since kicad-cli 9 misreads junctions on unsplit wires (measured). Check overlap scenarios with a GUI-cleanup emulation, because kicad-cli cannot see that failure.
- **Orientation.** All 12 orientations on R, D and an asymmetric transistor, asserting which pin KiCad puts on N, for both tools. Include an "already wired outward" variant, which catches the slice-ordering hazard. Add KiCad-authored fixtures: demos/flat_hierarchy/pic_programmer.kicad_sch and vme-wren/clocks C116, repathed.
- **Stock-sheet regression sweep.** On repathed demo sheets (so unnamed nets export), wire every eligible pin to a fresh name and to an existing name, and run connect_pins over nearby pairs. Assert clean in kicad-cli, in the GUI emulation and in the lint. This is the sweep that exposed P2 and P3.
- **Scenario fixtures:**
  - S1-S9;
  - every item kind at the stub end, on the stub, at P and within 1 IU;
  - label stacking; labels at crossings;
  - buses with shared entry points (royalblue54L_feather) and bus-member leaks (probe-buses s07, s13);
  - sheet-pin ports with child power and global names, single- and multi-instance;
  - a sheet pin on a wire interior;
  - unit-0 pads on one sheet and across sheets, with local, global and power N (R_Network03_Split, 4xxx_IEEE, the 74LS125 and TL072 demos);
  - reused sheets with differing units;
  - no_connect-type pins; stacked-pad names; duplicate references; an unresolved lib_name;
  - PWR_FLAG nets; rule areas and directives;
  - the decoupling cap at 4 rotations on C and C_Small, including a cap pin on a mid-wire label (e1), with write counts;
  - refusals asserting byte identity.
- **Model differential.** On repathed demo leaf sheets and random canonical sheets: C never joins pins kicad-cli keeps apart, and P0 plus name closure covers every kicad-cli join. The prototype passed 75 sheets (measured). Add an overlap fixture judged by the GUI emulation (critique e2).
- **Unit tests without KiCad** for each contact clause and name source, so CI without KiCad covers the logic.
- **Rewrite tests that encode bugs:**
  - test_avoids_conflicting_label and TestStubCollision: refuse, checked by netlist.
  - test_auto_direction_rotated_90: geometric outward.
  - test_wire_pins_to_net_auto_junction and the routing cases in TestAutoJunctions: no routing junctions; D3 becomes a refusal.
  - Messages and geometry pinned in test_cst.py and test_snap_grid_precision.py.
  - connect_pins raising ValueError instead of ToolError (meta tests).
  - The KiCad 10 gate tests that pin junction geometry (test_cst.py around lines 1111-1150 and 1267-1280), which fail only on ci/** pushes.
- **KiCad 10.**
  - Run the suite on a ci/** branch.
  - Branch the stacking tests on the exact kicad-cli version and log it, since Windows pins 10.0.5 and macOS is unpinned.
  - Keep a KiCad-10-only fixture set (local power, jumper groups, stacked pin numbers such as `[1-4]`), skipped on 9.
- **Performance.** A 50-pin call on vme-wren fpga-power under a budget, for example 3 s, and a sub-sheet call including the cross-sheet scan.
- **Skills.** Update the schematic-design error table: retry without refused pins, and pass the pad numbers a refusal lists (LW-08).

## 7. Open questions for the user

1. **Ports with no other name.** Pins whose net reaches a sheet pin and carries no other name: refuse (about 1% of demo parent-sheet pins), or follow the port into the child file. *Recommendation:* refuse now with a working remedy, and revisit port following on usage data. Frequency in GUI-drawn designs other than KiCad's demos is unknown.
2. **N exists elsewhere in the project as a power or global name, but not on this sheet.** Today every design silently creates `/sub/GND` (M at least reports "new local net"). The options:
   - refuse with a remedy (*recommended*);
   - write the local label with a warning;
   - place a power symbol or global label automatically.

   The remedy points at add_power_symbol, which must first become one write. Automatic placement would change the PCB net names the e2e tests pin (T10).
3. **Existing files.** Since 6769e15, every auto-junction this repo wrote landed on an unsplit wire. kicad-cli 9, the reader behind `update_pcb_from_schematic`, drops wire halves in such files, so boards built on KiCad 9 hosts may already be wrong. The options:
   - an opt-in repair tool plus a release note (*recommended*);
   - repair on the next write, which edits bytes the user did not ask to change;
   - a refusal or warning when touching such geometry.
4. **Splitting an existing wire node.** Does splitting a wire at a point where the caller asked for a T count as the requested change under the ADR invariant? add_wires and add_junctions need it to stop writing truncating junctions, and the optional connect_pins tee needs it. *Recommendation:* yes, recorded in the ADR status log, with a node-level byte-preservation check.

## 8. Confidence and what remains unverified

**Confidence:**
- *High:* P1, P2, P5 and P8 (measured on synthetic fixtures and stock or demo sheets, each re-measured by a verifier). P4 and P7: high on mechanism (measured on synthetic fixtures and generated layouts), unmeasured on frequency in real designs.
- *High:* the KiCad 9 contact and naming rules (measured twice).
- *Medium:* P3 and the GUI parts of P2 and P9. The mechanism is sourced and the effect emulated; eeschema was never driven.
- *Medium:* section 5 as a whole. `mine_impl` implements most of it, and its strict touch rule caused no bad write in any probe.
  - The text departs from the prototype in five places, each toward allowing more: overlaps handled in C; the cap's coincidence no-op; proceeding when only P0 says the pin is on N; name closure; the predicate in place of the 0.05 mm tolerance. The first two produced measured failures in an earlier draft of this section; both have since been narrowed (5.2, 5.6).
  - These refinements were measured only in isolation, if at all: separate bus subgraphs, sheet-pin capture, stacked-pad names, 3-segment routes, the cross-sheet pad check.
  - Port following is untested.
  - Re-run the probe categories against an implementation of the text before shipping.

**Needs KiCad 10 (CI runners); all sourced only:**
- local power symbols `(power local)` as sheet-scoped names;
- jumper groups and `duplicate_pin_numbers_are_jumpers`;
- stacked pin numbers such as `[1-4]`;
- the label stacking change in 10.0.6;
- junction pass-through joins;
- bus-entry ends on wire interiors;
- collinear overlaps in kicad-cli 10;
- the KiCad-order transform and the instance unit rule;
- whether `(power global)` parses only in 10-stamped files;
- whether ERC's dropped-marker bug is fixed.

**Not measured:**
- the revised design's refusal rate on real work-in-progress schematics (only 3 of 718 demo candidates were free pins; any figure is a projection);
- eeschema drag behaviour (latent T contacts);
- symbol-body overlap, which neither design checks and 3-segment routes make likelier;
- the cost of the 3-segment route search;
- which net KiCad's own PCB update binds for a pad listed in two nets (sourced: an unstable sort);
- live LLM sessions (the workflow probes simulated the skills' plans).
