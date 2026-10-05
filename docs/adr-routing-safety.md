# Routing safety: decision record

Status: approved 2026-10-04 as design Hb2. The design, the alternatives it was chosen over and
every figure below come from the routing pressure test of 2026-10-01 to 2026-10-04: a worker
session that built prototypes against commit 54160c1 and judged each tool call by comparing
kicad-cli 9.0.8 netlists before and after, node set by node set (**measured**), and again after an
emulation of the KiCad 9 GUI's load-time cleanup (**emulated**; eeschema itself was never driven).
A manager re-measured two claims independently. There is no KiCad 10 reading of any of it. The
reports and the harness live in a scratch directory (`%TEMP%\pr57work` and `%TEMP%\pr57pt`), which
is not durable, so this record restates what the design depends on and names the report each
figure came from (see Sources).

## The problem

wire_pins_to_net and connect_pins edit geometry, and KiCad derives connectivity from geometry.
Before this work they could merge unrelated nets, split nets, or wire the wrong pin, and report
success. Three measured examples:
- 16 of 36 generated layouts built the way the repo's skills prescribe came out shorted
  (pressure-test-report.md section 1, probe LW-13, worker).
- The wrong pin was wired on rotated and mirrored parts in 4 of 12 orientations: the repo mirrored
  before rotating and KiCad rotates first, so the computed point was the true pin reflected
  through the symbol origin, which is the other pin of a resistor or diode and empty space on a
  transistor (manager, `mgr_verify.py`; source read of KiCad 9.0 sch_io_kicad_sexpr_parser.cpp
  and sch_symbol.cpp SetOrientation).
- Routing junctions cut wires in kicad-cli 9, the reader behind this repo's netlist export, ERC
  and PCB update: a junction on the interior of an unsplit wire keeps only the half from the
  wire's first point to the junction (manager, `mgr_verify.py`; the 9 GUI and KiCad 10 connect
  it).

## The contract (fixed)

- **Delivery.** On success every requested pad, in every placed copy on this sheet, is on N, or
  the two pins are connected.
- **Nothing else changes.** No other nets merge or split, no pad ends up in two nets, no existing
  net is renamed, and no net's class set changes except the joined net's.
- **Atomic.** One write per call. A refusal leaves the file byte-identical and lists, per pin,
  the reason code, the obstacle and a remedy.

## ADR-R1: model only what KiCad does stably, and refuse whatever reaches the rest

**Decision.** Design Hb2. Model only what KiCad does that is stable and fundamental, and refuse
outright whenever the pin's possible net reaches something that is not modelled.

**Evidence.** Three designs were built and run on identical inputs (hybrid-report.md sections 3
and 4; dupref-report.md section 5):

| Design | What it is | Wrong writes | Refusal rate, generated layouts / demo pins |
|---|---|---|---|
| M2 | a precise model of KiCad's rules | 24 | 17.3% / 36.9% |
| E | writes only into empty space, no model of existing wiring | 0 | 20.1% / 100% |
| Hb2 | this design | 0, in both readings | 17.3% / 36.9% |

- M2's 24 wrong writes were mostly cases it modelled wrongly: sheet pins on wire interiors, pad
  copies on other sheets, unresolved symbols, duplicate references (hybrid-report.md section 3).
- E refused almost every edit to an existing design and left 99 planned pins off their net with
  adaptive retries, where the other designs left 0.
- Hb2's extra refusals over M2 come from the outright refusals and the pad-copy rule below; some
  of them blocked M2's wrong writes, and the rest are the price of not modelling those items.
- One probe call has no verdict in any design: it uses a KiCad 10 `(power local)` symbol that
  kicad-cli 9 cannot load.

**Pin geometry, the prerequisite for everything.**
- KiCad's transform order, rotate then mirror, in integer internal units (0.0001 mm), for both
  the position and the outward direction. A `(mirror)` written before `(at)` is ignored, as
  KiCad's parser ignores it.
- The library symbol is resolved as KiCad resolves it: `lib_name` if present, else `lib_id`,
  exact match, no fallback.
- The unit comes from the instance entry for this sheet's path; body style; hidden pins
  included; the effective electrical type includes a placed alternate.

**Two views of existing connectivity, built once per call.**
- The **possible view** decides refusals. Anything within a 0.05 mm margin of a point or line
  joins it; 2-point graphic lines count as lines; plain crossings do not join; same-text names
  join transitively.
- The **narrow view** decides "already on N". It holds only the joins every KiCad reader makes.
  It may miss a connection; the cost is a redundant label, measured clean in every sample.
- **Names, by KiCad's rule only:** every label kind; a power symbol's Value; a hidden power_in
  pin's name. PWR_FLAG names nothing (its pin is power_out).

**Outright refusals.** Refuse when the pin's possible net reaches a bus or bus entry, a sheet pin,
text containing `${`, a symbol with jumper groups, a unit-0 pad whose units are not all placed on
this sheet, a symbol whose instances disagree on its unit, or a netclass label or rule area unless
every joined part certainly carries one class set. Refuse the whole call if any symbol on the
sheet is derived or unresolved.

**Decisions per pad, in order.**
1. The narrow view says the pad is on N in every copy: no-op.
2. The possible view carries a different name: refuse, naming it.
3. Otherwise wire it, report the pin's existing net-mates, and say when N is a new local net on
   this sheet.

**Pad identity.** A pad is (reference, pad number) across the placed units of one part. Every
copy on this sheet is wired; a copy on another sheet refuses.

## ADR-R2: new geometry touches only the target pin

**Decision.** New items may touch only the target pin's connection point. A new wire's interior
passes no item, overlaps no wire or bus, and crosses no existing line. The label is captured by
nothing but its own stub, or sits on the pin. Routing never writes a junction.

**Evidence.** Junctions on unsplit wires split nets in kicad-cli 9, and collinear overlaps split
nets in the GUI reading (pressure-test-report.md section 3, problems P2 and P3). The crossing ban
is kept because a later call by another tool that puts a label or junction on such a crossing
merges the two nets: after a stub crossing net A's wire, `add_label` or `add_global_label` at the
crossing merged /A and /N in both readings, and `add_junctions` or an `add_wires` that
auto-junctions there merged them in both readings and split /N in kicad-cli (hybrid-report.md
section 6, `crossban_probe.out`). The ban cost 20 connect_pins refusals across every sample; in
wire_pins_to_net it never refused, it only swapped a stub for a label on the pin.

## ADR-R3: a shared reference is one part only when the pad maps agree

**Decision.** Two placed symbols sharing a reference are one part, and allowed, only if each
places a distinct unit and their library definitions agree on which unit draws each pad number,
with the same electrical type. Otherwise refuse. References are read from live instance paths
only, the ones KiCad uses for this project's hierarchy.

**Evidence** (dupref-report.md sections 3 to 5). Of the 63 calls where an earlier rule
(byte-identical library entries) refused, 58 lift: 19 clean writes, 27 correct no-ops, and 12
refused for other reasons the old refusal hid. The 4 true duplicates (probe PI-15) still refuse.
The rule is stricter than KiCad in two synthetic cases (dupref-report.md section 4, cases A and
B), edited library variants of one chip that differ in a pad's type or in the unit drawing it;
that costs refusals only, and no real sample reached either. KiCad's own netlist never joins
copies of a pad and carries no part-identity judgement; its only one-part notion is ERC's
`different_unit_net`.

The live-path refinement was not measured: a leftover reference on a dead instance path caused
the one remaining unnecessary refusal (tiny_tapeout's U3). Its implementation test must show that
case lifting and the true duplicates still refusing.

## ADR-R4: the tools

**wire_pins_to_net** (slice 1).
- Validate: label_text non-empty and trimmed, no leading `/`, no bus syntax, not an auto-name
  pattern (`Net-(`, `unconnected-(`), no `${`; direction from the closed set; stub_length a
  positive multiple of 1.27 mm; duplicate pins removed.
- Resolve pins: an exact pad number wins; a name matching pads drawn at one point is one target;
  a name matching pads at different points refuses and lists them; no_connect-type pins and pins
  with an NC flag refuse.
- Geometry: the outward stub at 1x length, else a label on the pin. A longer stub contains the
  shorter one, so it cannot pass when 1x fails; the sideways and inward fallbacks go.
- One write.

**connect_pins** (slice 2).
- Routes: straight, then horizontal-first L, then vertical-first L, each through the touch rule.
  No label, no junction.
- No-op when already connected; refuse when the two sides carry different names, or when either
  is a multi-copy pad. The automatic `Net-(ref-pin)` label goes: it made later legitimate calls
  refuse.
- Skill guidance, from measured recovery (hybrid-report.md section 7): after a `touch` refusal,
  retry with wire_pins_to_net and one fresh shared name; after a `names` refusal, use the name the
  message reports; otherwise stop and report. The low-level add_label is not a safe fallback: it
  wrote wrong in 41 of 67 `names`, `sheet_pin` and `bus` cases.

**get_net_connections** (slice 2) gains a pin-seeded mode in the same slice that removes the auto
label, because it finds connect_pins nets only through that label.

**auto_place_decoupling_cap** (slice 3): placement and both wirings composed on one parsed tree,
written once. Placed pin ends follow the touch rule; after placement, refuse if any pre-existing
net changed.

## Slices and tests

1. The pin transform fix, routing junctions removed, the overlap and crossing bans, the shared
   connectivity module, and wire_pins_to_net on it. These ship together: the transform fix alone
   makes the old stubs run along existing outward wires, where the kept junction truncates them
   (pressure-test-report.md section 5.7, critique e3, measured).
2. connect_pins and get_net_connections.
3. auto_place_decoupling_cap.

Tests the design requires: a netlist oracle comparing kicad-cli netlists before and after by node
set, reporting merges, splits, renames, class changes and pads in two nets; byte preservation on
every successful write and byte identity on every refusal; fixtures ported from the probe cases;
a model-versus-KiCad differential on every KiCad version CI carries, since the design's soundness
depends on KiCad not changing these rules; and the tests that blessed the old bugs rewritten.

Performance: the prototype's hierarchy scan took about 8 s per call on vme-wren, the largest demo
project, when that project was present (h_build\perf_h.out, design H). The product needs a cached
scan and a budget test.

## Out of scope, recorded

- Flattening derived symbols (queued separately; until then they refuse).
- Following sheet pins into child sheets, and bus support: refused for now; revisit on usage
  data.
- Existing files where earlier versions of this repo wrote junctions that cut wires in kicad-cli
  9: a separate read-only check plus a release note.
- add_wires and add_junctions still write junctions on unsplit wires, and add_label is
  unchecked. Separate issues.
- Files edited by earlier versions of this repo may hold no-connect flags and wires at the
  mirrored point the old transform gave a pin at rotation 90 or 270 with a mirror.
  remove_no_connect now looks at the pin KiCad draws and will not find such a flag. An
  inference, not measured; a read-only check could list them.

## Decisions (2026-10-04)

1. Hb2 is approved as specified.
2. N present as a power or global net elsewhere in the project but not on this sheet: write it,
   and warn in the result that N is a new local net on this sheet.
3. connect_pins loses its automatic `Net-(ref-pin)` label and refuses to join two differently
   named nets.

## Sources

All in the scratch directory named in the status line; dates are when the run was made.
- `pr57work\pressure-test-report.md` (2026-10-01): the first pressure test, the facts sections 2
  and 5 rely on, and the probe case IDs (PI-, HIER-, UP-, DEC-, INERT-, LW-).
- `pr57work\coarse-variant-report.md` (2026-10-02): the coarse refusal model against the precise
  one.
- `pr57work\hybrid-report.md` (2026-10-03): Hb against M2 and E, the crossing ban, and the
  recovery measurements.
- `pr57work\dupref-report.md` (2026-10-03): the pad-map rule for duplicate references, and the
  gate figures for Hb2.
- `pr57work\routing-proposal-v2.md` (2026-10-03, decisions 2026-10-04): the design this record
  restates.
- `pr57pt\HANDOFF.md` (2026-10-04): how to rerun the harness against an implementation.

## Status log

Each slice appends an entry when it lands.

- 2026-10-04, slice 1: the pin transform for every pin read; the shared model,
  `mcp_server_kicad/_connectivity.py`; the touch rule with the overlap and crossing bans; and
  wire_pins_to_net on it, with validation, resolution, every copy of a pad, the no-op and names
  decisions, the outright refusals, duplicate references, net classes and one write. Where the
  prototype (h2_impl with the crossing ban) and this record differed, the record was built:
  - a pin name matching pads drawn at one point is one target; the prototype refused any name
    matching two pads;
  - stub_length must be a multiple of 1.27 mm, and label_text trimmed with no leading "/";
  - references and units are read at the sheet's live instance paths, by KiCad 9.0.8's rule as
    its source reads: the entry for the path, else the first entry, which its parser makes the
    symbol's own, else the Reference property and the top-level unit. tiny_tapeout's U3 and U4
    now pass and PI-15's duplicates still refuse, which settles ADR-R3's open point. Checked per
    reference, a reused sheet whose instances share one is refused; the prototype accepted it;
  - remedies end "otherwise stop and report" and never suggest add_label;
  - the unit-0 rule covers any pad drawn by a unit not placed on the sheet, an inference that
    was not measured.
  Also in the slice: auto_place_decoupling_cap reports what a refused pin leaves on disk, and the
  hierarchy is parsed only where the bytes can hold what a call needs, so a cold call on vme-wren
  takes 1.7 to 3.7 s of CPU where it took 6.1 to 6.7 s (measured 2026-10-04; PR #68, commit
  8655b79). Measured along the way, with kicad-cli 9.0.8 (PR #68, commit ef8e270): the model's two
  views agree with its netlist on all 93 demo sheets; on a sweep of 42 sampled calls on three demo
  sheets, main wrote 5 wrong (3 named merges, 2 splits) and failed the write checks 20 times, 2 of
  those among the 5, where this slice wrote none wrong and passed every check; its ERC crashes on
  a sheet whose lib_name names no lib_symbols entry; and it cannot load a symbol with
  jumper_pin_groups. IC14 on vme-wren is 20 symbols under 4 library names, not the 5 variants
  dupref-report.md counts. Left for later: connect_pins, get_net_connections and the composed
  auto_place_decoupling_cap (slices 2 and 3); the `Net-(...)` label connect_pins still writes,
  which wire_pins_to_net then refuses as [names] while its message names the remove_label call
  that clears it; netclass patterns in a .kicad_pro; and stacked pin numbers such as `[1-4]`,
  which resolve only as literal text. KiCad 10 is checked by the macOS and Windows jobs only.

  A review of the branch then found five defects, each fixed with a test that failed first. A
  pad was identified by its symbol's Reference property while the reference check used the
  reference KiCad reads at the live path, and KiCad's own smps-com demo carries a stale property
  (R2 on eight resistors KiCad reads as R2 to R9), so one name could select several parts and
  wire them as one pad; parts are now known by the references KiCad gives them. The touch rule
  missed a bus entry's body at a stub end and a graphic line's end on a stub, both of which the
  possible view joins, a gap the prototype had too. auto_place_decoupling_cap dropped its
  wiring's notes, among them the decision-2 warning. The note for a same-named sheet pin claimed
  a join kicad-cli 9.0.8 does not make. And a stub_length of 1e305 overflowed instead of
  refusing; lengths are now bounded at 1000 mm.

  The pressure test's harness then ran the branch as a design on the prototype's 4,816 inputs,
  each write judged in both readings (the gate; `pr57pt\coarse\dupref\gate_x_final2.json`). It
  found four more defects, each fixed with a test that failed first: a stub_length under
  0.00005 mm rounded to 0 and passed validation; a placed alternate was counted as possibly
  naming the net by either name, although KiCad's source (sch_pin.cpp, 9.0.8 and 10.0.6) takes
  the type from the alternate and the name only from the library pin, and six DEC-15 calls were
  refused for it; resolution kept only the first name of a pad number, so the 74278's pad 6,
  drawn as Y4 and as ~{P1}, could not be asked for by the second; and a refusal for a unit not
  drawn here named the symbol's top-level unit rather than the one KiCad draws at the live
  path. After them: no write judged wrong except five where the harness keyed a stacked pin
  name (U1:VSS) instead of its pads, all clean and delivered when re-judged by pad number; no
  false no-op except DEC-23 and two replays of those same calls; refusal rates at or below the
  prototype's except probeC, 31.5% against 30.0%, where all 11 extra refusals are stub lengths
  that are not multiples of 1.27 mm; and no planned pin left off its net by the adaptive
  policies, as with the prototype, while the skill policies left fewer off (26 of 147 against
  40; 169 of 1,196 against 302). The first full pass ran on 4f9861a; the later commits were run
  only on the inputs they can reach (scan_fix_reach.py and scan_multiname.py list them), and on
  the adaptive policies, whose refusal parsing the gate's first adapter had broken. A review of
  the PR then found the pin reads behind get_pin_positions, get_net_connections, connect_pins
  and the no-connect tools taking a part's definition by lib_id alone, where KiCad and the model
  take the entry its lib_name names; they now share the model's rule (`lib_key`), and still find
  a part by its Reference property and draw its top-level unit.

  A second review of the PR, with mutation testing, found more defects, each fixed with a test
  that failed first, and one piece of dead code. A placed symbol whose library name lib_symbols
  holds twice (a hand edit or a merge leaves that) was drawn from the first entry while KiCad
  draws the last, and a wire was written to the wrong pad; such a symbol now refuses the whole
  call, and the pin reads take the last entry as KiCad does. Names were compared as written, while
  KiCad reads {slash} as "/" in library names and names a label's net by its unescaped text, so
  KiCad's own pic_programmer demo had a net no call could reach; names and library keys are now
  compared as KiCad reads them. Several messages claimed more than was true: the unit-0 refusal,
  the count of pins wired, the remedy for a connect_pins label, a refused call's planned lines and
  the decoupling cap's remedies and undo. The netlist judge passed a renamed user net inside the
  join, and split names at their last "/". The touch rule's overlap check could never fire and
  went. These fixes reach the gate only where a sheet carries a {slash} escape (pic_programmer
  and tiny_tapeout; no gate sheet holds a library name twice), so the gate ran again on 8d10aa4
  over those inputs, 73 sweep calls and 9 probe scripts (247 records), and every outcome matched
  the run before, call for call (`pr57pt\coarse\dupref\gate_x_final3.json`).
