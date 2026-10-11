"""Fixture writers for the inert rerun, copied from the originals (read-only sources):
  harness.py                   place, splice, _edit, _n, wire, bus, bus_entry, junction,
                               no_connect, label, directive          (used by probe-inert/s*.py)
  probe-inert/common.py        add_pro (netclass_template.kicad_pro, copied next to this file),
                               poly, text, rule_area, raw_label
  verify-probe-inert/vlib.py   new_sheet -> rlib.fresh, write_pro, place, _splice, wire, bus,
                               bus_entry, junction, no_connect, label, netclass_flag, polyline,
                               rule_area                              (prefixed v_ here)
  probe-inert/s05_hidden_nc.py pt.kicad_sym (symbol HN), written per job into rlib.newdir("libs")
  verify-probe-inert/v02_latent.py vlib_custom.kicad_sym (symbol VH), written per job likewise
Only the output locations changed: fixtures go to rlib.fresh() (the per-job scratch directory).
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import rlib
from mcp_server_kicad import _cst, schematic

HERE = Path(__file__).resolve().parent
PRO_TEMPLATE = HERE / "netclass_template.kicad_pro"


# ----------------------------------------------------------------------- harness.py (copied)


def place(p, lib, ref, x, y, rot=0, mirror="", value=None):
    return schematic.place_component(
        lib_id=lib, reference=ref, value=value or ref, x=x, y=y, rotation=rot,
        mirror=mirror, schematic_path=p,
    )


def _edit(p, fn):
    tree = _cst.parse(open(p, "rb").read())
    fn(tree.lists[0])
    open(p, "wb").write(_cst.serialize(tree))


def splice(p, token: str, sexpr: bytes):
    node = _cst.parse(sexpr).lists[0]
    u = node.find("uuid")
    if u is not None:
        u.atoms[1].set_text(str(uuid.uuid4()))
    _edit(p, lambda root: schematic._splice_sch_node(root, token, node))


def _n(v):
    s = f"{round(v, 4):.4f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def wire(p, x1, y1, x2, y2):
    splice(p, "wire", f"(wire (pts (xy {_n(x1)} {_n(y1)}) (xy {_n(x2)} {_n(y2)})) "
           f"(stroke (width 0) (type default)) (uuid \"x\"))".encode())


def bus(p, x1, y1, x2, y2):
    splice(p, "bus", f"(bus (pts (xy {_n(x1)} {_n(y1)}) (xy {_n(x2)} {_n(y2)})) "
           f"(stroke (width 0) (type default)) (uuid \"x\"))".encode())


def bus_entry(p, x, y, dx=2.54, dy=2.54):
    splice(p, "bus_entry", f"(bus_entry (at {_n(x)} {_n(y)}) (size {_n(dx)} {_n(dy)}) "
           f"(stroke (width 0) (type default)) (uuid \"x\"))".encode())


def junction(p, x, y):
    splice(p, "junction", f"(junction (at {_n(x)} {_n(y)}) (diameter 0) (color 0 0 0 0) "
           f"(uuid \"x\"))".encode())


def no_connect(p, x, y):
    splice(p, "no_connect", f"(no_connect (at {_n(x)} {_n(y)}) (uuid \"x\"))".encode())


def label(p, text, x, y, rot=0, kind="label", shape="input"):
    shape_s = f"(shape {shape}) " if kind != "label" else ""
    splice(p, kind, f"({kind} \"{text}\" {shape_s}(at {_n(x)} {_n(y)} {rot}) "
           f"(effects (font (size 1.27 1.27))) (uuid \"x\"))".encode())


def directive(p, netclass, x, y, rot=0):
    splice(p, "netclass_flag", f"(netclass_flag \"\" (length 2.54) (shape round) "
           f"(at {_n(x)} {_n(y)} {rot}) (effects (font (size 1.27 1.27)) (justify left bottom)) "
           f"(uuid \"x\") (property \"Netclass\" \"{netclass}\" (at {_n(x)} {_n(y)} 0) "
           f"(effects (font (size 1.27 1.27) (italic yes)) (justify left))))".encode())


# ----------------------------------------------------------------- probe-inert/common.py (copied)


def mk(name: str) -> str:
    return rlib.fresh(name)


def add_pro(p: str) -> None:
    """Write a .kicad_pro with the schematic's stem that defines net classes HV and LV."""
    pro = Path(p).with_suffix(".kicad_pro")
    data = json.loads(PRO_TEMPLATE.read_text(encoding="utf-8"))
    data["meta"]["filename"] = pro.name
    pro.write_text(json.dumps(data, indent=2), encoding="utf-8")


def poly(p, pts):
    xy = " ".join(f"(xy {_n(x)} {_n(y)})" for x, y in pts)
    splice(p, "polyline", f"(polyline (pts {xy}) (stroke (width 0) (type default)) "
           f"(uuid \"x\"))".encode())


def text(p, s, x, y):
    splice(p, "text", f"(text \"{s}\" (exclude_from_sim no) (at {_n(x)} {_n(y)} 0) "
           f"(effects (font (size 1.27 1.27)) (justify left bottom)) (uuid \"x\"))".encode())


def rule_area(p, pts):
    xy = " ".join(f"(xy {_n(x)} {_n(y)})" for x, y in pts)
    splice(p, "rule_area", f"(rule_area (polyline (pts {xy}) (stroke (width 0) (type dash)) "
           f"(fill (type none)) (uuid \"x\")))".encode())


def raw_label(p, s, x, y, kind="label"):
    shape = "(shape input) " if kind != "label" else ""
    splice(p, kind, f"({kind} \"{s}\" {shape}(at {x} {y} 0) (effects (font (size 1.27 1.27)))"
           f" (uuid \"x\"))".encode())


# ----------------------------------------------------------- verify-probe-inert/vlib.py (copied)


def v_new_sheet(case: str) -> str:
    return rlib.fresh(case)


def v_write_pro(p: str, classes=("HV",)) -> None:
    """Minimal .kicad_pro next to the schematic defining extra net classes."""
    def cls(name, prio):
        return {"name": name, "priority": prio, "clearance": 0.2, "track_width": 0.25,
                "via_diameter": 0.8, "via_drill": 0.4, "microvia_diameter": 0.3,
                "microvia_drill": 0.1, "diff_pair_width": 0.2, "diff_pair_gap": 0.25,
                "diff_pair_via_gap": 0.25, "wire_width": 6, "bus_width": 12, "line_style": 0,
                "pcb_color": "rgba(0, 0, 0, 0.000)", "schematic_color": "rgba(0, 0, 0, 0.000)"}
    pro = Path(p).with_suffix(".kicad_pro")
    data = {"meta": {"filename": pro.name, "version": 3},
            "net_settings": {"classes": [cls("Default", 2147483647)]
                             + [cls(c, i) for i, c in enumerate(classes)],
                             "meta": {"version": 4}, "net_colors": None,
                             "netclass_assignments": None, "netclass_patterns": []}}
    pro.write_text(json.dumps(data, indent=2), encoding="utf-8")


def v_place(p, lib, ref, x, y, rot=0, value=None, **kw):
    return schematic.place_component(lib_id=lib, reference=ref, value=value or ref, x=x, y=y,
                                     rotation=rot, schematic_path=p, **kw)


def v_splice(p, token, text_):
    tree = _cst.parse(Path(p).read_bytes())
    root = tree.lists[0]
    node = _cst.parse(text_.encode()).lists[0]
    u = node.find("uuid")
    if u is not None:
        u.atoms[1].set_text(str(uuid.uuid4()))
    schematic._splice_sch_node(root, token, node)
    Path(p).write_bytes(_cst.serialize(tree))


def v_wire(p, x1, y1, x2, y2, token="wire"):
    v_splice(p, token, f'({token} (pts (xy {x1} {y1}) (xy {x2} {y2})) '
                       f'(stroke (width 0) (type default)) (uuid "u"))')


def v_bus(p, x1, y1, x2, y2):
    v_wire(p, x1, y1, x2, y2, token="bus")


def v_bus_entry(p, x, y, dx, dy):
    v_splice(p, "bus_entry", f'(bus_entry (at {x} {y}) (size {dx} {dy}) '
                             f'(stroke (width 0) (type default)) (uuid "u"))')


def v_junction(p, x, y):
    v_splice(p, "junction", f'(junction (at {x} {y}) (diameter 0) (color 0 0 0 0) (uuid "u"))')


def v_no_connect(p, x, y):
    v_splice(p, "no_connect", f'(no_connect (at {x} {y}) (uuid "u"))')


def v_label(p, text_, x, y, kind="label", rot=0):
    shape = "(shape input) " if kind != "label" else ""
    v_splice(p, kind, f'({kind} "{text_}" {shape}(at {x} {y} {rot}) '
                      f'(effects (font (size 1.27 1.27)) (justify left bottom)) (uuid "u"))')


def v_netclass_flag(p, cls, x, y, rot=0):
    v_splice(p, "netclass_flag",
             f'(netclass_flag "" (length 2.54) (shape round) (at {x} {y} {rot}) '
             f'(effects (font (size 1.27 1.27)) (justify left bottom)) (uuid "u") '
             f'(property "Netclass" "{cls}" (at {x} {y} 0) '
             f'(effects (font (size 1.27 1.27) (italic yes)) (justify left))))')


def v_polyline(p, pts):
    xy = " ".join(f"(xy {x} {y})" for x, y in pts)
    v_splice(p, "polyline", f'(polyline (pts {xy}) (stroke (width 0) (type default)) (uuid "u"))')


def v_rule_area(p, pts):
    xy = " ".join(f"(xy {x} {y})" for x, y in pts)
    v_splice(p, "rule_area", f'(rule_area (polyline (pts {xy}) (stroke (width 0) (type dash)) '
                             f'(fill (type none)) (uuid "u")))')


# ------------------------------------------------------------------- custom symbol libraries


def _hn_pin(etype, x, y, ang, name, num, hide=False):
    h = "\n\t\t\t\t(hide yes)" if hide else ""
    return (f"\t\t\t(pin {etype} line\n\t\t\t\t(at {x} {y} {ang})\n\t\t\t\t(length 2.54){h}\n"
            f"\t\t\t\t(name \"{name}\" (effects (font (size 1.27 1.27))))\n"
            f"\t\t\t\t(number \"{num}\" (effects (font (size 1.27 1.27))))\n\t\t\t)\n")


def hn_lib() -> str:
    """probe-inert/s05_hidden_nc.py's pt.kicad_sym (symbol HN), written into this job's scratch."""
    lib = rlib.newdir("hnlib") / "pt.kicad_sym"
    lib.write_text(
        "(kicad_symbol_lib\n\t(version 20241209)\n\t(generator \"pt\")\n"
        "\t(symbol \"HN\"\n\t\t(exclude_from_sim no)\n\t\t(in_bom yes)\n\t\t(on_board yes)\n"
        "\t\t(property \"Reference\" \"U\" (at 0 10.16 0) (effects (font (size 1.27 1.27))))\n"
        "\t\t(property \"Value\" \"HN\" (at 0 -10.16 0) (effects (font (size 1.27 1.27))))\n"
        "\t\t(property \"Footprint\" \"\" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))\n"
        "\t\t(property \"Datasheet\" \"\" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))\n"
        "\t\t(symbol \"HN_1_1\"\n"
        + _hn_pin("passive", -7.62, 0, 0, "A", "1")
        + _hn_pin("passive", 7.62, 0, 180, "B", "2", hide=True)
        + _hn_pin("no_connect", 0, 7.62, 270, "NC", "3")
        + _hn_pin("power_in", 0, -7.62, 90, "VCC", "4", hide=True)
        + "\t\t)\n\t)\n)\n",
        encoding="utf-8",
    )
    return str(lib)


def vh_lib() -> str:
    """verify-probe-inert/v02_latent.py's vlib_custom.kicad_sym (symbol VH), per job."""
    lib = rlib.newdir("libs") / "vlib_custom.kicad_sym"
    lib.write_text(
        '(kicad_symbol_lib (version 20241209) (generator "verify")\n'
        ' (symbol "VH" (exclude_from_sim no) (in_bom yes) (on_board yes)\n'
        '  (property "Reference" "U" (at 0 7.62 0) (effects (font (size 1.27 1.27))))\n'
        '  (property "Value" "VH" (at 0 -7.62 0) (effects (font (size 1.27 1.27))))\n'
        '  (property "Footprint" "" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))\n'
        '  (property "Datasheet" "" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))\n'
        '  (symbol "VH_1_1"\n'
        '   (pin passive line (at -5.08 0 0) (length 2.54)\n'
        '    (name "A" (effects (font (size 1.27 1.27)))) (number "1" (effects (font (size 1.27 1.27)))))\n'
        '   (pin power_in line (at 0 -5.08 90) (length 2.54) (hide yes)\n'
        '    (name "VDD" (effects (font (size 1.27 1.27)))) (number "2" (effects (font (size 1.27 1.27)))))\n'
        '   (pin no_connect line (at 5.08 0 180) (length 2.54)\n'
        '    (name "NC" (effects (font (size 1.27 1.27)))) (number "3" (effects (font (size 1.27 1.27)))))\n'
        '  )\n )\n)\n', encoding="utf-8")
    return str(lib)
