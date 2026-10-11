# Routing fix, v2: design for approval

2026-10-03. Replaces routing-proposal.md. Basis: pressure-test-report.md, coarse-variant-report.md and hybrid-report.md (worker session, kicad-cli 9.0.8 netlists plus an emulation of the KiCad 9 GUI's load-time cleanup). Figures below are the worker's measurements unless marked otherwise; the manager re-measured two of them independently (pr57work\mgr_verify.py) and spot-checked others against raw outputs. Current main is 7620ed4: the auto PWR_FLAG is already gone (#62), and place_component and pin lookup already refuse derived symbols (#65).

## 1. The problem

wire_pins_to_net and connect_pins edit geometry, and KiCad derives connectivity from geometry. Today they can merge unrelated nets, split nets, or wire the wrong pin, and report success. Measured examples: 16 of 36 generated layouts shorted under today's code (worker); the wrong pin wired on rotated and mirrored parts in 4 of 12 orientations (manager, mgr_verify.py); routing junctions that cut wires in kicad-cli 9, the reader behind this repo's netlist export, ERC and PCB update (manager, mgr_verify.py).

## 2. Contract

- **Delivery.** On success, every requested pad (every placed copy on this sheet) is on N, or the two pins are connected.
- **Nothing else changes.** No other nets merge or split, no pad ends up in two nets, no existing net is renamed, and no net's class set changes except the joined net's.
- **Atomic.** One write per call. A refusal leaves the file byte-identical and lists, per pin, the reason code, the obstacle and a remedy.
- **Measured status of the design below (Hb2):** 0 wrong writes and 0 false no-ops across every sample the worker ran, in both readings, with the duplicate-reference rule of section 4.6 (dupref-report.md section 5). One probe call has no verdict in any design: it uses a KiCad 10 symbol that kicad-cli 9 cannot load.

## 3. Why this design

Three designs were built and measured on identical inputs:
- **M2**, a precise model of KiCad's rules: made 24 wrong writes, mostly in cases it modelled wrongly (sheet pins on wires, pad copies on other sheets, unresolved symbols, duplicate references).
- **E**, "write only into empty space", with no model of existing wiring: 0 wrong writes, but it refuses almost every edit to an existing design (100% of demo wire_pins_to_net calls) and leaves 99 planned pins unwired where the others leave 0.
- **Hb2** (Hb with the corrected duplicate-reference rule), below: 0 wrong writes and refusal rates close to M2's (17.3% for both on generated layouts; 36.9% for both on demo pins). Its extra refusals over M2 come from the outright refusals in 4.3 and the pad-copy rule in 4.5: some of them blocked M2's wrong writes, and the rest are the price of not modelling those items.

The principle behind Hb2: model only what KiCad does that is stable and fundamental, and refuse outright whenever the pin's net reaches something not modelled.

## 4. Design Hb2

### 4.1 Pin geometry (prerequisite for everything)
- KiCad's transform order, rotate then mirror, in integer IU, for both position and outward direction. Today's code mirrors first.
- Library symbol resolved as KiCad does: lib_name if present, else lib_id, exact match.
- Unit from the instance entry for this sheet path; body style; hidden pins included; effective electrical type including a placed alternate.

### 4.2 Two views of existing connectivity, built once per call
- **Possible view (decides refusals):** anything within a 0.05 mm margin of a point or line joins it; graphic 2-point lines count as lines; plain crossings do not join; same-text names join transitively.
- **Narrow view (decides "already on N"):** only joins every KiCad reader makes. It may miss a connection; the cost is a redundant label, measured clean in every sample.
- **Names, KiCad's rule only:** all label kinds; a power symbol's Value; a hidden power_in pin's name. PWR_FLAG names nothing.

### 4.3 Outright refusals (the source of the safety margin over M2)
Refuse when the pin's possible net reaches: a bus or bus entry, a sheet pin, text containing `${`, a symbol with jumper groups, a unit-0 pad whose units are not all placed on this sheet, a symbol whose instances disagree on its unit, or a netclass label or rule area unless every joined part certainly carries one class set. Refuse the whole call if any symbol on the sheet is derived or unresolved.

### 4.4 Decisions per pad, in order
1. Narrow view says the pad is on N in every copy: no-op.
2. Possible view carries a different name: refuse, naming it.
3. Otherwise wire it. Report the pin's existing net-mates, and say when N is a new local net on this sheet.

### 4.5 Pad identity
A pad is (reference, pad number) across the placed units of one part. Every copy on this sheet is wired; a copy on another sheet refuses.

### 4.6 Duplicate references
Two placed symbols sharing a reference are one part, and allowed, only if each places a distinct unit and their library definitions agree on which unit draws each pad number, with the same electrical type. Otherwise refuse.

Measured (dupref-report.md): of the 63 calls where the earlier byte-identical rule refused, 58 lift (19 clean writes, 27 correct no-ops, 12 refused for other reasons the old refusal hid). The 4 true duplicates still refuse. The rule is stricter than KiCad in one synthetic case (two edited library variants of one chip); that costs refusals only and no real sample reached it.

References are read from live instance paths only, the ones KiCad uses for this project's hierarchy. A leftover reference on a dead path caused the one remaining unnecessary refusal (tinytapeout U3). This refinement is unmeasured; its implementation test must show that case lifting and the true duplicates still refusing.

### 4.7 New geometry (touch rule)
New items may touch only the target pin's connection point. A new wire's interior passes no item, overlaps no wire or bus, and crosses no existing line. The label is captured by nothing but its own stub, or sits on the pin. Routing never writes a junction.

The crossing ban is kept: a later call by another repo tool that puts a label or junction on such a crossing merges the two nets (hybrid-report.md section 6). It cost 20 connect_pins refusals across all samples.

### 4.8 wire_pins_to_net
- **Validate:** label_text non-empty and trimmed, no leading `/`, no bus syntax, not an auto-name pattern (`Net-(`, `unconnected-(`), no `${`; direction from the closed set; stub_length a positive multiple of 1.27 mm; duplicate pins removed.
- **Resolve pins:** an exact pad number wins; a name matching pads drawn at one point is one target; a name matching pads at different points refuses and lists them; no_connect-type pins and pins with an NC flag refuse.
- **Geometry:** the outward stub at 1x length, else a label on the pin. The current sideways and inward fallbacks, and the 2x and 3x lengths, go: a longer stub contains the shorter one, so it cannot pass when 1x fails.
- **One write.**

### 4.9 connect_pins
- Routes: straight, then horizontal-first L, then vertical-first L, each through the touch rule. No label, no junction.
- No-op when already connected; refuse when the two sides carry different names, or when either is a multi-copy pad.
- The automatic `Net-(ref-pin)` label goes. It made later legitimate calls refuse.
- **Refusal guidance for the skill** (measured recovery, hybrid-report.md section 7): after a `touch` refusal, retry with wire_pins_to_net and one fresh shared name; after a `names` refusal, use the name the message reports; otherwise stop and report. The low-level add_label is not a safe fallback (it wrote wrong in 41 of 67 such cases).

### 4.10 auto_place_decoupling_cap
Placement and both wirings composed on one parsed tree, written once. Placed pin ends follow the touch rule; after placement, refuse if any pre-existing net changed.

### 4.11 get_net_connections
Gains a pin-seeded mode in the same slice that removes the auto label, because it currently finds connect_pins nets only through that label.

## 5. Implementation

**Slices, each shippable and tested end to end:**
1. Pin transform fix, routing junctions removed, overlap and crossing bans, the shared connectivity module, and wire_pins_to_net on it. These ship together: the transform fix alone makes today's stubs run along existing wires and split nets (worker, measured).
2. connect_pins and get_net_connections.
3. auto_place_decoupling_cap.

**Performance:** the hierarchy scan for duplicate references and pad copies took about 8 s per call on the largest demo project (worker, prototype). The product needs a cached raw-bytes scan; target a budget test on vme-wren.

**Tests:**
- An oracle helper that compares kicad-cli netlists before and after by node set, reporting merges, splits, renames, class changes and pads in two nets.
- Byte preservation on every successful write, and byte identity on every refusal.
- Fixtures ported from the worker's probe cases, including the 12 orientations, the S1 to S9 shorts, sheet pins, unit-0 pads, duplicate references, buses and netclasses, plus a stock-sheet sweep. Skipped without kicad-cli; unit tests of each rule run everywhere.
- A model-versus-KiCad differential in CI on every KiCad version the runners carry, since the design's soundness depends on KiCad not changing these rules.
- Tests that currently bless the bugs are rewritten: test_avoids_conflicting_label, TestStubCollision, test_auto_direction_rotated_90, test_wire_pins_to_net_auto_junction and the routing cases in TestAutoJunctions.
- skills/schematic-design: the connect_pins fallback at line 99 becomes the refusal guidance in 4.9.

**Each commit message covers** what changed, the measured before-state with its source, and the tests that pin it. The ADR status log gets an entry for each slice.

## 6. Out of scope, recorded

- Flattening derived symbols (queued separately; until then they refuse).
- Following sheet pins into child sheets, and bus support: refused for now; revisit on usage data.
- Existing files where earlier versions of this repo wrote junctions that cut wires in kicad-cli 9: a separate read-only check plus a release note.
- add_wires and add_junctions still write junctions on unsplit wires, and add_label is unchecked. Separate issues.

## 7. Decisions (made 2026-10-04)

1. **Hb2 is approved as specified.**
2. **N present as a power or global net elsewhere in the project but not on this sheet:** write it, and warn in the result that N is a new local net on this sheet.
3. **connect_pins** loses its automatic `Net-(ref-pin)` label and refuses to join two differently named nets.
