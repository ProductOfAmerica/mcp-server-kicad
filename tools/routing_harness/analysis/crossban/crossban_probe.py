"""Crossing-ban question for design H (h_impl.py): does a new stub or route that crosses an
existing line's interior at a clean point ever produce a join or split, in one call or in a
call sequence? Readings: kicad-cli (cli, measured) and judge3's GUI load-time CleanUp
emulation (gui, emulated; not available in this repository, so every gui verdict prints n/a),
judged by judge3.snapshot2/assess2 (wire_pins_to_net: probe-based on N; other calls: groups as
listed, N=None).

Fixture (mm): R1 at (101.6, 101.6), pin 1 end (101.6, 97.79) outward up, so H's 1x stub runs
(101.6, 97.79)-(101.6, 95.25). The crossed line runs along y = 96.52, so the crossing point is
X = (101.6, 96.52). R2:1 (76.2, 97.79) rises to (76.2, 96.52) and the wire W runs from there to
(127, 96.52) with local label 'A' at (127, 96.52): W is net A. R3:1 is already on 'N' by its own
stub and label at x = 139.7.

Sections:
  S  single H calls whose stub or route crosses: a wire, a bus, a 2-point graphic line, a wire
     carrying a label and a junction elsewhere, a wire ending at a sheet pin, an existing
     crossing of two wires, and a connect_pins L route.
  T  the touch rule refusing crossings where an item sits within r of the crossing.
  Q  sequences: H then H (model readout, and H on a pin placed at X), H then the repo's own
     tools (the target's; 54160c1 in the pressure test: add_label, add_global_label,
     add_junctions, add_wires), each with a control that runs the same second call without H's
     crossing; then the same sequences with h_impl.CROSS_BAN = True.
Output: stdout (crossban_probe.out in the pressure test). Scratch files go under the run's
crossban/tmp.
Run: uv run python tools/routing_harness/analysis/crossban/crossban_probe.py
"""

import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)

HB = hpaths.suite_out("crossban")
TMP = HB / "tmp" / f"p{os.getpid()}"
TMP.mkdir(parents=True, exist_ok=True)
os.environ["TMP"] = os.environ["TEMP"] = str(TMP)
tempfile.tempdir = str(TMP)
import harness as H  # noqa: E402
import judge3 as J  # noqa: E402
import h_impl as HI  # noqa: E402
from mcp_server_kicad import _cst, project, schematic  # noqa: E402

R = "Device:R"
X = (101.6, 96.52)
GOOD = {"cli": None, "gui": None}


def verdict(res):
    out = []
    for rd in ("cli", "gui"):
        r = res.get(rd) or {}
        bits = [k for k in ("bad_merges", "splits", "renames", "class_bad", "pads_multi",
                            "named_merge", "req_pad_multi") if r.get(k)]
        w = r.get("wrong")
        # a reading that was not taken (the gui reading here) is n/a, never clean
        tag = "WRONG" if w else "clean" if w is False else (
            "n/a (gui reading not available)" if r.get("unavailable") else "n/a")
        out.append(f"{rd}={tag}"
                   + (f"({', '.join(f'{b}={r[b]}' for b in bits)})" if bits else "")
                   + f" dlv={r.get('delivered')}")
    if res.get("false_noop"):
        out.append("FALSE-NOOP")
    return " ".join(out)


def base(name, line="wire", extra=None):
    """The fixture, with the crossed line of the given kind along y = 96.52."""
    p = H.fresh(name)
    H.place(p, R, "R1", 101.6, 101.6)
    H.place(p, R, "R2", 76.2, 101.6)
    H.place(p, R, "R3", 139.7, 101.6)
    H.wire(p, 139.7, 97.79, 139.7, 95.25)
    H.label(p, "N", 139.7, 95.25)
    if line == "wire":
        H.wire(p, 76.2, 97.79, 76.2, 96.52)
        H.wire(p, 76.2, 96.52, 127, 96.52)
        H.label(p, "A", 127, 96.52)
    elif line == "bus":
        H.bus(p, 88.9, 96.52, 114.3, 96.52)
    elif line == "gline":
        H.splice(p, "polyline", b'(polyline (pts (xy 88.9 96.52) (xy 114.3 96.52)) '
                 b'(stroke (width 0) (type default)) (uuid "x"))')
    if extra:
        extra(p)
    return p


def wptn(p, ref, pin, N, **kw):
    snap = J.snapshot2(p, p, N=N)
    st, msg = J.call(HI.wire_pins_to_net, pins=[{"reference": ref, "pin": pin}], label_text=N,
                     schematic_path=p, **kw)
    res = J.assess2(snap, p, [{(ref, pin)}], edited_path=p, status=st) if st != "REFUSED" else {}
    return st, msg, res


def show(tag, st, msg, res):
    first = msg.split("\n")[1] if "\n" in msg else msg
    print(f"{tag}: {st} {verdict(res) if res else ''}\n    {first[:300]}", flush=True)


def repo_step(p, fn, **kw):
    """A repo call judged with no requested join (any merge or split is wrong)."""
    snap = J.snapshot2(p, p)
    st, msg = J.call(fn, schematic_path=p, **kw)
    res = J.assess2(snap, p, [], edited_path=p, status=st) if st == "OK" else {}
    return st, msg, res


def model_readout(p, refs):
    _, root, *_ = schematic._open_sch_cst(p)
    m = HI._Model(root, p)
    m.ensure()
    cm = m.coarse()
    out = []
    for ref, num in refs:
        c = m.pads[(ref, num)][0]
        out.append(f"{ref}:{num} C-names {sorted(m.names_of('C', c))} coarse-names"
                   f" {sorted({t for _, t in cm.names_of(c)})}")
    return "; ".join(out)


def copy(p, tag):
    d = Path(tempfile.mkdtemp(prefix=f"{tag}_"))
    q = d / Path(p).name
    shutil.copy2(p, q)
    return str(q)


# ------------------------------------------------------------------------------- S: single calls
print("=== S: single H calls crossing an existing line at a clean point", flush=True)


def label_junction(p):
    H.label(p, "A", 114.3, 96.52)  # alone on W's interior
    H.wire(p, 88.9, 96.52, 88.9, 99.06)  # a T into W's interior with a junction
    H.junction(p, 88.9, 96.52)


def existing_x(p):
    H.wire(p, 99.06, 93.98, 104.14, 99.06)  # a diagonal wire crossing W exactly at X


for tag, line, extra in (("S1 wire (net A)", "wire", None), ("S2 bus", "bus", None),
                         ("S3 2-point graphic line", "gline", None),
                         ("S4 wire with a label and a junction elsewhere", "wire", label_junction),
                         ("S6 existing crossing of two wires at X", "wire", existing_x)):
    p = base(tag.split()[0], line, extra)
    show(tag, *wptn(p, "R1", "1", "N"))
    print("    after:", model_readout(p, [("R1", "1"), ("R2", "1")]) if line == "wire"
          else model_readout(p, [("R1", "1")]), flush=True)

# S5: W ends at a sheet pin (the coarse model reaches the sheet pin only through W)
d = Path(tempfile.mkdtemp(prefix="S5_")) / "s5"
project.create_project(str(d), "s5")
root, pro, kid = str(d / "s5.kicad_sch"), str(d / "s5.kicad_pro"), str(d / "kid.kicad_sch")
project.create_schematic(kid)
project.add_hierarchical_sheet(root, "kid", kid, pins=[{"name": "PWR", "direction": "input"}],
                               x=127, y=93.98, project_path=pro)
ends = [w for w in _cst.parse(open(root, "rb").read()).lists[0].find_all("wire")]
sx = min(float(xy.atoms[1].text) for w in ends for xy in w.find("pts").find_all("xy"))
for args in (("R1", 101.6), ("R2", 76.2), ("R3", 139.7)):
    schematic.place_component(lib_id=R, reference=args[0], value=args[0], x=args[1], y=101.6,
                              schematic_path=root, project_path=pro)
H.wire(root, 139.7, 97.79, 139.7, 95.25)
H.label(root, "N", 139.7, 95.25)
H.wire(root, 76.2, 97.79, 76.2, 96.52)
H.wire(root, 76.2, 96.52, sx, 96.52)
show("S5 wire ending at a sheet pin's stub (sheet pin PWR at (127, 96.52))",
     *wptn(root, "R1", "1", "N"))

# S7: connect_pins L route crossing W. R5 at (114.3, 88.9): pin 2 end (114.3, 92.71). The route
# R1:1 (101.6, 97.79) -> (114.3, 97.79) -> (114.3, 92.71) crosses W at (114.3, 96.52).
p = base("S7")
H.place(p, R, "R5", 114.3, 88.9)
snap = J.snapshot2(p, p)
st, msg = J.call(HI.connect_pins, ref1="R1", pin1="1", ref2="R5", pin2="2", schematic_path=p)
res = J.assess2(snap, p, [{("R1", "1"), ("R5", "2")}], edited_path=p, status=st) \
    if st != "REFUSED" else {}
show("S7 connect_pins L route crossing W", st, msg, res)

# ------------------------------------------------------------------------------- T: touch rule
print("\n=== T: crossings with an item within r of the crossing point (touch rule)", flush=True)
for tag, extra in (
    ("T1 junction at X", lambda p: H.junction(p, *X)),
    ("T2 label at X", lambda p: H.label(p, "Q", *X)),
    ("T3 a wire end 0.03 mm from X", lambda p: (H.wire(p, 101.63, 96.52, 101.63, 93.98))),
    ("T4 W split with its ends exactly at X", None),
):
    if extra is None:
        p = H.fresh("T4")
        H.place(p, R, "R1", 101.6, 101.6)
        H.place(p, R, "R2", 76.2, 101.6)
        H.wire(p, 76.2, 97.79, 76.2, 96.52)
        H.wire(p, 76.2, 96.52, 101.6, 96.52)
        H.wire(p, 101.6, 96.52, 127, 96.52)
        H.label(p, "A", 127, 96.52)
    else:
        p = base(tag.split()[0], "wire", extra)
    show(tag, *wptn(p, "R1", "1", "N"))


# ------------------------------------------------------------------------------- Q: sequences
def sequences(ban: bool):
    HI.CROSS_BAN = ban
    print(f"\n=== Q: sequences, CROSS_BAN={ban}", flush=True)
    p0 = base(f"Q_ban{int(ban)}")
    p1 = copy(p0, "q1")
    st, msg, res = wptn(p1, "R1", "1", "N")
    show("Q0 step 1 H wire_pins_to_net([R1:1], 'N')", st, msg, res)
    print("    H model after step 1:", model_readout(p1, [("R1", "1"), ("R2", "1")]), flush=True)
    steps = (
        ("add_label('A' at X)", schematic.add_label, dict(text="A", x=X[0], y=X[1])),
        ("add_label('B' at X)", schematic.add_label, dict(text="B", x=X[0], y=X[1])),
        ("add_global_label('A' at X)", schematic.add_global_label,
         dict(text="A", x=X[0], y=X[1])),
        ("add_junctions([X])", schematic.add_junctions, dict(points=[{"x": X[0], "y": X[1]}])),
        ("add_wires(X -> (114.3, 96.52)) (auto-junction at X)", schematic.add_wires,
         dict(wires=[{"x1": X[0], "y1": X[1], "x2": 114.3, "y2": 96.52}])),
    )
    for i, (name, fn, kw) in enumerate(steps, 1):
        a = copy(p1, f"q{i}a")
        show(f"Q{i} H then repo {name}", *repo_step(a, fn, **kw))
        if not ban:
            c = copy(p0, f"q{i}c")
            show(f"Q{i}c control: repo {name} without H's step", *repo_step(c, fn, **kw))
    # H then H: place R7 so its pin 2 end lands on X, then H on R7:2.
    a = copy(p1, "q9")
    schematic.place_component(lib_id=R, reference="R7", value="R7", x=101.6, y=92.71,
                              schematic_path=a)
    print("    R7:2 at", HI.pin_positions(a, "R7")["2"], flush=True)
    show("Q9 H then repo place_component(R7, pin 2 end at X) then H wire_pins_to_net([R7:2],"
         " 'M')", *wptn(a, "R7", "2", "M"))
    HI.CROSS_BAN = False


sequences(False)
sequences(True)
