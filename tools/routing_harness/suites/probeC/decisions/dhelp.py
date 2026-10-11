"""dhelp: fixture writers for the decisions rerun, copied from probe-decisions/dlib.py (prefix d_)
and verify-probe-decisions/vlib.py (prefix v_) with their geometry unchanged, plus two runner
additions that rlib v2 did not have (both now live in rlib v3, see below):

  run_sequence_ex  rlib.run_sequence plus fixture-action steps ({"action": fn, "label": ...}):
                   fn(edited_path_in_the_design_copy) is applied to each design's own copy
                   between design calls (e.g. the repo's remove_label remedy in v01 W4). No
                   record is written for an action.
  pad dedupe       rlib.fallbacks receives spec.pads as requested, so a call that lists one pad
                   twice ([RN1:1, RN1:1]) would get add_label twice on every copy (stacked
                   labels). The wrapper installed here passes the pads de-duplicated (order
                   kept); every other argument is unchanged.

Pin positions: dlib.pos / vlib.pinxy used mine_impl.pin_positions; m2_impl.pin_positions is the
same code (m2_impl docstring: pin geometry is mine_impl's, unchanged).
Import this module after nothing else: it imports rlib first.
"""

import shutil
import sys
import uuid
from pathlib import Path

import hpaths  # noqa: E402,F401  (an entry script has put it on sys.path)
import rlib as R  # noqa: E402
from rlib import H, M2  # noqa: E402
from mcp_server_kicad import _cst, project, schematic  # noqa: E402

RES = "Device:R"
SCRIPT_D = "probe-decisions/"
SCRIPT_V = "verify-probe-decisions/"


# (follow-up 1's fallback pad dedupe wrapper is not needed: rlib v3 de-duplicates itself)


# ------------------------------------------------------------------------- common
def pos(path, ref, num, i=0):
    """KiCad's own pin end (copy i of a multi-copy pad)."""
    x, y, _ = M2.pin_positions(str(path), ref)[num][i]
    return round(x, 4), round(y, 4)


def outward(path, ref, num, i=0):
    return M2.pin_positions(str(path), ref)[num][i][2]


pins = R.pins
wire = H.wire
label = H.label
junction = H.junction
no_connect = H.no_connect
bus = H.bus
bus_entry = H.bus_entry
fresh = H.fresh
hplace = H.place  # dlib.place: harness.place(p, lib, ref, x, y, rot=0, mirror="", value=None)

_DXY = {"right": (1, 0), "left": (-1, 0), "up": (0, -1), "down": (0, 1)}


# ------------------------------------------------------------------------- dlib copies
def d_stub_label(path, ref, num, text, length=2.54, kind="label", i=0):
    """Fixture writer: a raw stub outward from copy i of ref:num plus a label at its end."""
    x, y = pos(path, ref, num, i)
    dx, dy = _DXY[outward(path, ref, num, i)]
    ex, ey = round(x + dx * length, 4), round(y + dy * length, 4)
    wire(path, x, y, ex, ey)
    label(path, text, ex, ey, kind=kind)
    return ex, ey


def d_write_custom_lib(path):
    """A .kicad_sym with symbols for NC-type pins, hidden power_in pins and alternates."""

    def pin(etype, x, y, ang, name, num, hide=False, alts=()):
        h = "\n\t\t\t\t(hide yes)" if hide else ""
        a = "".join(f"\n\t\t\t\t(alternate \"{n}\" {t} line)" for n, t in alts)
        return (f"\t\t\t(pin {etype} line\n\t\t\t\t(at {x} {y} {ang})\n\t\t\t\t(length 2.54){h}\n"
                f"\t\t\t\t(name \"{name}\" (effects (font (size 1.27 1.27))))\n"
                f"\t\t\t\t(number \"{num}\" (effects (font (size 1.27 1.27)))){a}\n\t\t\t)\n")

    def sym(name, body):
        return (
            f"\t(symbol \"{name}\"\n\t\t(exclude_from_sim no)\n\t\t(in_bom yes)\n\t\t(on_board yes)\n"
            "\t\t(property \"Reference\" \"U\" (at 0 7.62 0) (effects (font (size 1.27 1.27))))\n"
            f"\t\t(property \"Value\" \"{name}\" (at 0 -7.62 0) (effects (font (size 1.27 1.27))))\n"
            "\t\t(property \"Footprint\" \"\" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))\n"
            "\t\t(property \"Datasheet\" \"\" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))\n"
            f"\t\t(symbol \"{name}_1_1\"\n" + body + "\t\t)\n\t)\n"
        )

    text = (
        "(kicad_symbol_lib\n\t(version 20241209)\n\t(generator \"pt\")\n"
        + sym("NCPART", pin("passive", -7.62, 0, 0, "A", "1")
              + pin("no_connect", 7.62, 0, 180, "NC", "2"))
        + sym("HPIN", pin("passive", -7.62, 0, 0, "A", "1")
              + pin("power_in", 7.62, 0, 180, "VCC", "2", hide=True)
              + pin("power_in", 0, -7.62, 90, "VIN", "3"))
        + sym("HALT", pin("passive", -7.62, 0, 0, "A", "1")
              + pin("power_in", 7.62, 0, 180, "VCC", "2", hide=True, alts=(("ALTP", "passive"),)))
        + sym("HALT2", pin("passive", -7.62, 0, 0, "A", "1")
              + pin("passive", 7.62, 0, 180, "VCC", "2", hide=True,
                    alts=(("PWRALT", "power_in"),)))
        + ")\n"
    )
    Path(path).write_text(text, encoding="utf-8")
    return str(path)


def d_place_custom(sch, lib_path, name, ref, x, y, project_path=""):
    return schematic.place_component(lib_id=f"pt:{name}", reference=ref, value=name, x=x, y=y,
                                     symbol_lib_path=str(lib_path), schematic_path=sch,
                                     project_path=project_path)


def d_set_alternate(sch, ref, num, alt):
    """Select alternate *alt* on placed pin *num* of *ref* (the (pin "N" ...) instance entry)."""
    tree = _cst.parse(open(sch, "rb").read())
    root = tree.lists[0]
    sym = next(s for s in root.find_all("symbol")
               if schematic._sym_property_cst(s, "Reference") == ref)
    for p in sym.find_all("pin"):
        if p.atoms[1].text == num:
            node = _cst.parse(f"(alternate \"{alt}\")".encode()).lists[0]
            p.append_child(node, b" ")
            break
    else:
        raise RuntimeError(f"{ref} has no pin entry {num}")
    open(sch, "wb").write(_cst.serialize(tree))


def d_add_unit(sch, ref, unit, x, y):
    """Copy ref's placed symbol as another unit at (x, y), instances unit included."""
    tree = _cst.parse(open(sch, "rb").read())
    root = tree.lists[0]
    src = next(s for s in root.find_all("symbol")
               if schematic._sym_property_cst(s, "Reference") == ref)
    node = src.copy()
    node.find("unit").atoms[1].set_text(str(unit))
    schematic._fill_at(node, x, y, 0)
    node.find("uuid").atoms[1].set_text(str(uuid.uuid4()))
    for pn in node.find_all("pin"):
        pn.find("uuid").atoms[1].set_text(str(uuid.uuid4()))
    for pr in node.find_all("property"):
        schematic._fill_at(pr, x, y)
    n = 0
    for proj in node.find("instances").find_all("project"):  # find_all: direct children only
        for path in proj.find_all("path"):
            u = path.find("unit")
            if u is not None:
                u.atoms[1].set_text(str(unit))
                n += 1
    assert n, "no (instances (project (path (unit N)))) entry to update"
    root.insert_after(src, node)
    open(sch, "wb").write(_cst.serialize(tree))


def d_remove_nodes(sch, token, pred):
    tree = _cst.parse(open(sch, "rb").read())
    root = tree.lists[0]
    n = 0
    for node in list(root.find_all(token)):
        if pred(node):
            root.remove_child(node)
            n += 1
    open(sch, "wb").write(_cst.serialize(tree))
    return n


def d_hier_fixture(name, pin_name="SP", child_extra=None, keep_child_local=True):
    """Root + one child sheet 'kid' with sheet pin *pin_name*; the parent auto label removed
    (dlib.hier_fixture, BASE = rlib's fixture dir)."""
    d = H.BASE / name
    project.create_project(str(d), "hp")
    root = str(d / "hp.kicad_sch")
    pro = str(d / "hp.kicad_pro")
    kid = str(d / "kid.kicad_sch")
    project.create_schematic(kid)
    project.add_hierarchical_sheet(root, "kid", kid, pins=[{"name": pin_name, "direction": "input"}],
                                   x=101.6, y=50.8, project_path=pro)
    removed = d_remove_nodes(root, "label", lambda n: n.atoms[1].text == pin_name)
    assert removed == 1, removed
    if not keep_child_local:
        d_remove_nodes(kid, "label", lambda n: n.atoms[1].text == pin_name)
    schematic.place_component(lib_id="Device:R", reference="R11", value="R11", x=27.94, y=29.21,
                              schematic_path=kid, project_path=pro)
    if child_extra:
        child_extra(kid, pro)
    schematic.place_component(lib_id="Device:R", reference="R1", value="R1", x=99.06, y=57.15,
                              schematic_path=root, project_path=pro)
    return root, pro, kid


# ------------------------------------------------------------------------- vlib copies
def v_new(name):
    return H.fresh("v_" + name)


def v_place(p, lib, ref, x, y, rot=0, value=None, project_path=""):
    return schematic.place_component(lib_id=lib, reference=ref, value=value or ref, x=x, y=y,
                                     rotation=rot, schematic_path=p, project_path=project_path)


def v_stubbed(p, ref, num, text, dx=0.0, dy=-2.54, kind="label", i=0):
    """Raw stub from ref:num by (dx, dy) and a label at its end."""
    x, y = pos(p, ref, num, i)
    ex, ey = round(x + dx, 4), round(y + dy, 4)
    wire(p, x, y, ex, ey)
    label(p, text, ex, ey, kind=kind)
    return ex, ey


def v_edit(path, fn):
    tree = _cst.parse(open(path, "rb").read())
    out = fn(tree.lists[0])
    open(path, "wb").write(_cst.serialize(tree))
    return out


def v_remove(path, token, pred):
    def f(root):
        n = 0
        for node in list(root.find_all(token)):
            if pred(node):
                root.remove_child(node)
                n += 1
        return n
    return v_edit(path, f)


def v_add_unit(path, ref, unit, x, y):
    """Copy ref's placed symbol as unit *unit* at (x, y); top-level and instance unit both set."""
    def f(root):
        src = next(s for s in root.find_all("symbol")
                   if schematic._sym_property_cst(s, "Reference") == ref)
        node = src.copy()
        node.find("unit").atoms[1].set_text(str(unit))
        _cst._fill_at(node, x, y, 0)
        node.find("uuid").atoms[1].set_text(str(uuid.uuid4()))
        for pn in node.find_all("pin"):
            pn.find("uuid").atoms[1].set_text(str(uuid.uuid4()))
        for pr in node.find_all("property"):
            _cst._fill_at(pr, x, y)
        hits = 0
        for proj in node.find("instances").find_all("project"):
            for ip in proj.find_all("path"):
                u = ip.find("unit")
                if u is not None:
                    u.atoms[1].set_text(str(unit))
                    hits += 1
        assert hits, "no instance unit to set"
        root.insert_after(src, node)
    v_edit(path, f)


def v_set_alt(path, ref, num, alt):
    def f(root):
        sym = next(s for s in root.find_all("symbol")
                   if schematic._sym_property_cst(s, "Reference") == ref)
        for pn in sym.find_all("pin"):
            if pn.atoms[1].text == num:
                pn.append_child(_cst.parse(f'(alternate "{alt}")'.encode()).lists[0], b" ")
                return
        raise RuntimeError(f"no pin entry {num} on {ref}")
    v_edit(path, f)


def v_write_lib(path):
    """Custom symbols: VNC (pin 2 no_connect type), VHP (pin 2 hidden power_in 'VDD'),
    VALTP (pin 2 hidden power_in 'VDD' with passive alternate 'PASV'), VALTW (pin 2 hidden
    passive 'VDD' with power_in alternate 'PWRIN'). Pins 1 and 2 at x = -5.08 / +5.08."""
    def pin(et, x, ang, name, num, hide=False, alts=()):
        h = "\n      (hide yes)" if hide else ""
        a = "".join(f'\n      (alternate "{n}" {t} line)' for n, t in alts)
        return (f"    (pin {et} line\n      (at {x} 0 {ang})\n      (length 2.54){h}\n"
                f'      (name "{name}" (effects (font (size 1.27 1.27))))\n'
                f'      (number "{num}" (effects (font (size 1.27 1.27)))){a}\n    )\n')

    def sym(name, body):
        return (f'  (symbol "{name}"\n    (exclude_from_sim no) (in_bom yes) (on_board yes)\n'
                '    (property "Reference" "U" (at 0 5.08 0) (effects (font (size 1.27 1.27))))\n'
                f'    (property "Value" "{name}" (at 0 -5.08 0) (effects (font (size 1.27 1.27))))\n'
                '    (property "Footprint" "" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))\n'
                '    (property "Datasheet" "" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))\n'
                f'    (symbol "{name}_1_1"\n' + body + "    )\n  )\n")

    p1 = pin("passive", -5.08, 0, "A", "1")
    text = ("(kicad_symbol_lib\n  (version 20241209)\n  (generator \"verify\")\n"
            + sym("VNC", p1 + pin("no_connect", 5.08, 180, "NC", "2"))
            + sym("VHP", p1 + pin("power_in", 5.08, 180, "VDD", "2", hide=True))
            + sym("VALTP", p1 + pin("power_in", 5.08, 180, "VDD", "2", hide=True,
                                    alts=(("PASV", "passive"),)))
            + sym("VALTW", p1 + pin("passive", 5.08, 180, "VDD", "2", hide=True,
                                    alts=(("PWRIN", "power_in"),)))
            + ")\n")
    Path(path).write_text(text, encoding="utf-8")
    return str(path)


def v_place_lib(sch, lib, name, ref, x, y, project_path=""):
    return schematic.place_component(lib_id=f"vlib:{name}", reference=ref, value=name, x=x, y=y,
                                     symbol_lib_path=str(lib), schematic_path=sch,
                                     project_path=project_path)


def v_hierarchy(name, pin="PA", sx=127.0, sy=63.5, keep_parent_label=False):
    """Root + child 'sub' with one sheet pin *pin*; returns (root, pro, kid, parent_stub_end)
    (vlib.hierarchy, BASE = rlib's fixture dir)."""
    d = H.BASE / ("v_" + name)
    project.create_project(str(d), "vp")
    root, pro, kid = str(d / "vp.kicad_sch"), str(d / "vp.kicad_pro"), str(d / "sub.kicad_sch")
    project.create_schematic(kid)
    project.add_hierarchical_sheet(root, "sub", kid, pins=[{"name": pin, "direction": "input"}],
                                   x=sx, y=sy, project_path=pro)
    lab = v_edit(root, lambda r: [(n.find("at").atoms[1].text, n.find("at").atoms[2].text)
                                  for n in r.find_all("label") if n.atoms[1].text == pin])
    assert len(lab) == 1, lab
    stub_end = (float(lab[0][0]), float(lab[0][1]))
    if not keep_parent_label:
        assert v_remove(root, "label", lambda n: n.atoms[1].text == pin) == 1
    return root, pro, kid, stub_end


# ------------------------------------------------------------------------- calls
def W(ref_pins, N, **kw):
    return dict(pins=pins(*ref_pins), label_text=N, **kw)


def C(r1, p1, r2, p2):
    return dict(ref1=r1, pin1=str(p1), ref2=r2, pin2=str(p2))


def wstep(ref_pins, N, label_=""):
    return dict(tool="wire_pins_to_net", kw=W(ref_pins, N), label=label_)


def cstep(r1, p1, r2, p2, label_=""):
    return dict(tool="connect_pins", kw=C(r1, p1, r2, p2), label=label_)


def run_sequence_ex(case, variant, steps, edited, **kw):
    """rlib v3's run_sequence takes fixture-action steps itself ({"action": fn, "label": ...});
    this name is kept for the copied scripts."""
    return R.run_sequence(case, variant, steps, edited, **kw)
