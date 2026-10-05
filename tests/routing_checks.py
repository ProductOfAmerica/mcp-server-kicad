"""Checks every routing write must pass, independent of the code under test.

``call_wptn`` runs wire_pins_to_net and holds it to the invariant in docs/adr-cst-substrate.md:
a refusal or a no-op leaves the file byte-identical, and a write only adds nodes, keeping every
existing node byte for byte and in order. The repo's single-span byte helpers in conftest cannot
express that, because a write adds wires after the last wire and labels after the last label,
two insertion points, so the comparison is per top-level node.

``lint_new_geometry`` is the structural half, computed from the file alone with no pin model, so
it does not trust the module it checks: no junction written, no new wire collinear with or
crossing an existing line, no new endpoint or label on an existing line's interior. The overlap
case is the one kicad-cli cannot see at all (the KiCad 9 GUI merges overlapping wires on load).
"""

from __future__ import annotations

from pathlib import Path

from mcp.server.mcpserver.exceptions import ToolError

from mcp_server_kicad import _cst, schematic

Point = tuple[int, int]
Seg = tuple[Point, Point]


def _iu(text: str) -> int:
    v = float(text) * 10000.0
    return int(v + 0.5) if v >= 0 else int(v - 0.5)


def _root(data: bytes):
    return _cst.parse(data).lists[0]


def _children(data: bytes) -> list[bytes]:
    return [_cst.serialize(c) for c in _root(data).children]


def assert_only_added(before: bytes, after: bytes, kinds=("wire", "label")) -> list:
    """*after* is *before* plus new top-level nodes of *kinds*; returns the new nodes.

    Every existing child must survive byte for byte, in order, and the bytes around the root
    list must not move.
    """
    tb, ta = _cst.parse(before), _cst.parse(after)
    assert tb.close_sep == ta.close_sep, "bytes after the root list changed"
    rb, ra = tb.lists[0], ta.lists[0]
    assert (rb.sep, rb.close_sep) == (ra.sep, ra.close_sep), "root list framing changed"
    old = [_cst.serialize(c) for c in rb.children]
    added = []
    i = 0
    for child in ra.children:
        data = _cst.serialize(child)
        if i < len(old) and data == old[i]:
            i += 1
            continue
        head = child.head if child.kind == "list" else None
        assert head in kinds, f"unexpected change at child {i}: {data[:120]!r}"
        added.append(child)
    assert i == len(old), f"{len(old) - i} existing node(s) changed or vanished"
    return added


def _pts(node) -> list[Point]:
    pts = node.find("pts")
    if pts is None:
        return []
    return [(_iu(p.atoms[1].text), _iu(p.atoms[2].text)) for p in pts.find_all("xy")]


def _lines(root) -> list[Seg]:
    """Every connectable line: wires, buses, bus entries and 2-point graphic polylines."""
    out: list[Seg] = []
    for node in root.lists:
        if node.head in ("wire", "bus"):
            xy = _pts(node)
            out += list(zip(xy, xy[1:]))
        elif node.head == "polyline":
            xy = _pts(node)
            if len(xy) == 2:
                out.append((xy[0], xy[1]))
        elif node.head == "bus_entry":
            at, size = node.find("at"), node.find("size")
            x, y = _iu(at.atoms[1].text), _iu(at.atoms[2].text)
            dx, dy = _iu(size.atoms[1].text), _iu(size.atoms[2].text)
            out.append(((x, y), (x + dx, y + dy)))
    return out


def _points(root) -> list[Point]:
    """Point items that connect: line ends, junctions, labels, NC flags, sheet pins."""
    out: list[Point] = []
    for a, b in _lines(root):
        out += [a, b]
    for node in root.lists:
        if node.head in (
            "junction",
            "no_connect",
            "label",
            "global_label",
            "hierarchical_label",
            "netclass_flag",
            "directive_label",
        ):
            at = node.find("at")
            out.append((_iu(at.atoms[1].text), _iu(at.atoms[2].text)))
        elif node.head == "sheet":
            for pin in node.find_all("pin"):
                at = pin.find("at")
                out.append((_iu(at.atoms[1].text), _iu(at.atoms[2].text)))
    return out


def _cross(o: Point, a: Point, b: Point) -> int:
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def _on_interior(p: Point, seg: Seg) -> bool:
    a, b = seg
    if a == b or p in (a, b) or _cross(a, b, p) != 0:
        return False
    return min(a[0], b[0]) <= p[0] <= max(a[0], b[0]) and min(a[1], b[1]) <= p[1] <= max(a[1], b[1])


def _overlap(s: Seg, t: Seg) -> bool:
    (a, b), (c, d) = s, t
    if _cross(a, b, c) != 0 or _cross(a, b, d) != 0 or a == b:
        return False
    axis = 0 if a[0] != b[0] else 1
    lo1, hi1 = sorted((a[axis], b[axis]))
    lo2, hi2 = sorted((c[axis], d[axis]))
    return min(hi1, hi2) - max(lo1, lo2) > 0


def _proper_cross(s: Seg, t: Seg) -> bool:
    (a, b), (c, d) = s, t
    d1, d2 = _cross(c, d, a), _cross(c, d, b)
    d3, d4 = _cross(a, b, c), _cross(a, b, d)
    return d1 * d2 < 0 and d3 * d4 < 0


def lint_new_geometry(before: bytes, after: bytes) -> None:
    """Fail when a routing write adds geometry that could join, split or merge on a reload."""
    rb, ra = _root(before), _root(after)
    assert len(ra.find_all("junction")) == len(rb.find_all("junction")), "a junction was written"
    old_lines, old_points = _lines(rb), _points(rb)
    old_wires = {_cst.serialize(w) for w in rb.find_all("wire")}
    old_labels = {_cst.serialize(n) for n in rb.find_all("label")}
    new_segs: list[Seg] = []
    for w in ra.find_all("wire"):
        if _cst.serialize(w) in old_wires:
            continue
        xy = _pts(w)
        for seg in zip(xy, xy[1:]):
            # Earlier new wires count too: two pins of one call must not stack their stubs.
            for line in old_lines + new_segs:
                assert not _overlap(seg, line), f"new wire {seg} overlaps {line}"
                assert not _proper_cross(seg, line), f"new wire {seg} crosses {line}"
            new_segs.append(seg)
            for end in seg:
                for line in old_lines:
                    assert not _on_interior(end, line), f"new end {end} on the interior of {line}"
            for p in old_points:
                assert not _on_interior(p, seg), f"existing item at {p} on new wire {seg}"
    for lab in ra.find_all("label"):
        if _cst.serialize(lab) in old_labels:
            continue
        at = lab.find("at")
        p = (_iu(at.atoms[1].text), _iu(at.atoms[2].text))
        for line in old_lines:
            assert not _on_interior(p, line), f"new label at {p} on the interior of {line}"


def call_wptn(path, pins, label_text, **kw) -> tuple[str, str]:
    """wire_pins_to_net with the write checks. Returns (status, message).

    status is "REFUSED" (ToolError, file byte-identical), "NOOP" (message starts "No change",
    file byte-identical) or "OK" (only wires and labels added, geometry lint clean).
    """
    path = Path(path)
    before = path.read_bytes()
    writes = []
    real = schematic._atomic_write

    def counting(target, data):
        writes.append(Path(target).name)
        return real(target, data)

    schematic._atomic_write = counting
    try:
        msg = schematic.wire_pins_to_net(pins, label_text, schematic_path=str(path), **kw)
    except ToolError as e:
        assert path.read_bytes() == before, "a refusal changed the file"
        assert writes == [], f"a refusal wrote {writes}"
        return "REFUSED", str(e)
    finally:
        schematic._atomic_write = real
    after = path.read_bytes()
    if msg.startswith("No change") or not pins:
        assert after == before, "a no-op changed the file"
        assert writes == [], f"a no-op wrote {writes}"
        return "NOOP", msg
    assert writes == [path.name], f"expected one write of {path.name}, got {writes}"
    assert after != before, f"success reported but nothing written: {msg}"
    assert_only_added(before, after)
    lint_new_geometry(before, after)
    return "OK", msg
