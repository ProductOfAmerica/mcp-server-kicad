# TODO

## ~~Schematic Layout Quality~~ (DONE — Plugin Skill)

Layout conventions are now delivered as the `schematic-design` skill inside
the `kicad` Claude Code plugin (`skills/schematic-design/SKILL.md`). The
skill is auto-invoked when Claude detects schematic design intent, teaching
placement spacing, signal flow direction, and wiring tool selection.

See `docs/superpowers/specs/2026-03-10-schematic-layout-plugin-design.md`
for the full design rationale.

## ~~Auto-Junction Bug in `connect_pins` / `wire_pins_to_net`~~ (FIXED)

Fixed via `_auto_junctions()` helper in schematic.py. Both `connect_pins` and
`wire_pins_to_net` now auto-insert junctions at T-intersections. Tests in
`tests/test_routing_tools.py::TestAutoJunctions`.
