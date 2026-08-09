# CST substrate architecture: spike-verified decision records

Status: drafted 2026-08-09 from workflow `issue9-arch-spikes` (3 Opus-max spikes, each independently re-executed by an adversarial verifier; verdicts A: PARTIAL-confirmed, B: CONFIRMED, C: PARTIAL-confirmed; corrections folded in below). Spike artifacts: scratchpad `spike_a/ spike_b/ spike_c/ verify_a/ verify_b/ verify_c/`; CI evidence: branch `ci/issue9-kicad10-probe` commits `eb2c7a1`/`57230ab`, run 31303313193.

## The invariant (fixed, non-negotiable)

Bytes the user did not ask us to change reach the disk unchanged, and any edit we cannot do correctly is refused with the file intact.

## ADR-1: the write substrate is a byte-preserving CST

**Decision.** A stdlib-only lossless concrete-syntax-tree module (`mcp_server_kicad/_cst.py`) becomes the substrate for file mutation. Bytes in, bytes out; whitespace and EOLs preserved; edits are node splices.

**Evidence.** Round-trip byte-identity 15,711/15,711 files, 423 MB (all stock symbol libs, all 15,415 stock footprints, KiCad 6/7/8 demo saves, fork testdata); verifier reproduced exactly and extended to 596 further files (594/596, see policy below). Splices preserve 100% of bytes outside the edited region and are semantically taken up by KiCad (ERC violation deltas, netlist net renames, DRC item-count changes on boards). Parse+serialize of the 2.4 MB Device.kicad_sym: ~1.07 s.

**Policies the spikes forced:**
- **Escape codec is the only lossy surface.** KiCad's contract, measured against its writers: unescape `\\ \" \n \r \t`, keep the backslash on unknown escapes, re-escape `\ " LF CR` on write, TAB emitted raw. Raw LF inside a quoted atom is a KiCad hard reject; BOM is a KiCad hard reject. The escape probe stays in the tree as a per-KiCad-version regression test. (kiutils gets this wrong today: it corrupts `\\` and `\n` payloads on read.)
- **Malformed input policy: refuse loudly.** KiCad's reader silently repairs broken files; its own 9.0.8 installer ships two malformed demos (unbalanced parens) that KiCad opens and the CST rejects. Consequence one: `kicad-cli` rc=0 proves loadability, never well-formedness, so byte-level checks stay the primary oracle in tests. Consequence two: the CST raises a positioned syntax error instead of guessing; same philosophy as the version guard.
- **Bytes-only I/O.** Text-mode reads silently rewrote every CRLF in a 5.9 MB board during spike B's first attempt. Inserted spans copy the EOL/indent of their anchor sibling (`insert_after(ref, node, sep=reuse)`), which is all the pretty-printing the substrate needs.

**Node API (everything the spikes needed, nothing more):** `parse(bytes) -> Node`, `serialize(Node) -> bytes`, `head / atoms / lists / find / find_all`, `atom.set_text`, `list.copy`, `parent.insert_after(ref, node, sep=None)`, `text` decode-on-demand.

**Open engineering debt, with a number attached:** prototype memory is 35-40x file size (222 MB for a 6 MB board). Production repr must fold separators into leaves or use a flat token array; kill criterion: if the hardened repr cannot hold the 6 MB Video board under ~10x, revisit the design before migrating board tools.

**Not building:** a typed full-file model, a pretty-printer, format documentation.

## ADR-2: new constructs come from harvested templates

**Decision.** Constructs the server creates are stamped from templates harvested from KiCad's own upgrade output (golden files), with parameter slots identified by differential inference: vary one input, upgrade the pair, diff atoms, classify IDENTITY / DERIVED / REFORMAT; noise (uuids) learned empirically from repeat runs, not by token name.

**Evidence.** Inference isolated exactly 1 slot among 226,984 atoms on the real Device library; handled one-input-to-many (symbol name fans into 3 atoms) and numeric reformatting; a stamped symbol survived two upgrade passes drift-free and rendered by name via `sym export svg`. Verifier re-ran the pipeline byte-identically and extended it unmodified to six untested constructs, including a property value containing escaped quotes, backslash, unicode, and spaces.

**Guardrails, each from a measured failure:**
1. Alignment must be content-keyed for lists KiCad sorts (pad `layers` produced a phantom slot under positional alignment).
2. Parameters that change structure (pad `smd` -> `thru_hole` adds `(drill …)`) are harvested as whole-subtree variants per value, never as atom slots.
3. Stamping must splice inside quote marks and encode through the ADR-1 codec; the prototype's `str.format` dropped quoting, so non-bareword values failed (closed, but failed).
4. Noise probes need n>=3 upgrade runs; content-derived ids (schematic hierarchy paths) must be classified explicitly, not assumed random.
5. **Board net references are version-gated with zero dialect overlap** (the sharpest spike-B result): `(net N)` in a KiCad 10 board is accepted and silently rebound by load order (a real miswire, measured: `(net 2)` landed on GND with no error), and `(net "NAME")` in a KiCad 9 board is a hard reject. Emit numeric nets for 9-format boards, named nets for 10+, and never copy a numeric net reference across versions. Schematic labels/wires, by contrast, overlap cleanly from KiCad 6 through 10 (verified: v6 file splice, EOF insertion, unicode and escaped payloads, all with netlist uptake).
6. sch/pcb harvesting needs the KiCad-10 CI runner (`sch upgrade`/`pcb upgrade` do not exist on 9; `pcb upgrade` also no-ops on current files, so re-serialization goldens come from `pcbnew.SaveBoard`). The first CI harvest must validate the noise probe on uuid-heavy sch/pcb output before templates from it are trusted. Also close there: the `.kicad_sch` writer escape contract (force a rewrite via `sch upgrade`), unverifiable locally on 9.

**Kill criterion.** If the CI harvest on sch/pcb shows noise indistinguishable from slots, fall back to hand-written emitters for those constructs; they still sit behind ADR-1 preservation, so the blast radius of a wrong emitter is one refused or wrong node, never a corrupted file.

## ADR-3: migration is a strangler fig by mutation-kind; the skeleton is add_label

**Decision.** kiutils (or the fork) remains the read layer. Write paths migrate one mutation-kind at a time; each migrated tool routes its save through CST splicing; unmigrated tools are untouched. Each slice is one PR, reverting cleanly, validated by (a) a byte-preservation test (everything outside the edited region unchanged), (b) the existing autouse ERC oracle, (c) the full suite, and (d) the now-gating KiCad 10 step on the macos runner (zero red baseline as of merge `a38f325`; validate pre-merge via a `ci/**` push, since that workflow does not run on PR branches).

**Walking skeleton: `add_label`.** Smallest RMW tool with full-stack value, and spike B proved its payload dialect is version-portable v6 through v10. The slice: `_cst.py` hardened from the spike prototype (leaner nodes, codec, malformed policy, escape self-check); `add_label` reimplemented as read-bytes -> CST -> splice template label -> write-bytes, kiutils-free on that path; tests for byte preservation, ERC uptake, and the corpus round-trip (stock libs swept on runners that have KiCad installed). Stretch, explicitly flagged: once CST-backed, `add_label` no longer needs the version guard for its write, because preservation holds by construction and the label dialect is portable; the guard then becomes a per-path capability check rather than a blanket refusal. That is the first user-visible payoff: a KiCad 10 user gets a working, safe edit before any parser grows KiCad 10 support.

**Order after the skeleton:** label/text family, then wires/junctions, then symbol placement (template harvest from CI), then board mutations last (they need ADR-2 guardrail 5 and the memory fix). Board side stays engine-ready: KiCad 11's headless `api-server` (~Feb-Mar 2027) slots in behind the same tool API.

**Kill criteria per slice:** byte-preservation test fails, ERC uptake missing, or the KiCad 10 gate goes red: revert the slice, keep the substrate.

## Standing corrections from verification (so the record stays honest)

- Spike A's "zero failures on real KiCad files" is scoped to well-formed files; KiCad-shipped malformed files exist and are refused by design.
- Spike A's spliced-file ERC delta is +2 violations (one dangling label, one multiple_net_names), not "3 items" as most naturally read.
- Spike C's counts were 8 fixture pairs / 32 harvest runs (7/8 fully explained), not 9/36; its "B" sizes were decoded character counts, not disk bytes.
- Spike B's KiCad-10 anchor-2 precedence case is inferred from the violation delta, proven only on KiCad 9.

## Status log

- 2026-08-09: slice 1 merged (CST substrate + byte-preserving add_label; kiutils retained for validation).
- 2026-08-09: slice 2: add_label made fully CST-native (page-size validation from the CST), which relaxes the version guard for exactly this path; KiCad 10 files get their first working edit. Every other tool keeps the guard. KiCad 10 e2e test mints a current-format schematic via sch upgrade on the gating macos runner.
