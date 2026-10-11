"""probeB shim for probe-units-pads/uplib.py: fixture helpers copied (add_unit, set_convert, set_fp,
copies, stub, probe_r, lpin/lsym/write_lib, place_custom), libraries written under <run>/probeB/libs,
triplicate() -> per-design copies for M2, H, Hb and E, and judge() running ONE design's call through
rlib.exec_design (judge3 on the netlist root). The script copies pass vid= and stepno=. The board
check of the original (update_pcb_from_schematic) is not part of judge3 and is not run."""

import hashlib
import os
import shutil
import sys
import uuid as _uuid

sys.dont_write_bytecode = True
import hpaths  # noqa: E402,F401
import rlib  # noqa: E402

from harness import (  # noqa: E402,F401
    BASE, _cst, _edit, _n, fresh, label, nets_coded, no_connect, place, schematic, wire,
)

M2 = rlib.M2
LIBS = str(rlib.OUT / "libs")
os.makedirs(LIBS, exist_ok=True)

IMPLS = {k: {"wire": m.wire_pins_to_net, "connect": m.connect_pins,
             "cap": m.auto_place_decoupling_cap} for k, m in rlib.MODS.items()}
_TOOL = {"wire": "wire_pins_to_net", "connect": "connect_pins", "cap": "auto_place_decoupling_cap"}


def sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()[:16]


def add_unit(p, ref, unit, x, y, top_unit=None, inst_unit=None, convert=None):
    def fn(root):
        src = schematic._find_syms_cst(root, ref)[0]
        node = src.copy()
        at = node.find("at")
        at.atoms[1].set_text(_n(x))
        at.atoms[2].set_text(_n(y))
        node.find("unit").atoms[1].set_text(str(unit if top_unit is None else top_unit))
        node.find("uuid").atoms[1].set_text(str(_uuid.uuid4()))
        for pn in node.find_all("pin"):
            u = pn.find("uuid")
            if u is not None:
                u.atoms[1].set_text(str(_uuid.uuid4()))
        for proj in node.find("instances").find_all("project"):
            for path in proj.find_all("path"):
                path.find("unit").atoms[1].set_text(str(unit if inst_unit is None else inst_unit))
        for prop in node.find_all("property"):
            pat = prop.find("at")
            pat.atoms[1].set_text(_n(x))
            pat.atoms[2].set_text(_n(y))
        if convert is not None:
            c = _cst.parse(f"(convert {convert})".encode()).lists[0]
            node.insert_after(node.find("unit"), c)
        root.insert_after(schematic._find_syms_cst(root, ref)[-1], node)

    _edit(p, fn)


def set_convert(p, ref, n, idx=0):
    def fn(root):
        node = schematic._find_syms_cst(root, ref)[idx]
        c = node.find("convert")
        if c is None:
            c = _cst.parse(f"(convert {n})".encode()).lists[0]
            node.insert_after(node.find("unit"), c)
        else:
            c.atoms[1].set_text(str(n))

    _edit(p, fn)


def set_fp(p, ref, fpid):
    def fn(root):
        for s in schematic._find_syms_cst(root, ref):
            for q in s.find_all("property"):
                if q.atoms[1].text == "Footprint":
                    q.atoms[2].set_text(fpid)

    _edit(p, fn)


def copies(p, ref, num):
    return M2.pin_positions(p, ref).get(num, [])


_DXY = {"right": (1, 0), "left": (-1, 0), "up": (0, -1), "down": (0, 1)}


def stub(p, x, y, d, text, length=2.54, kind="label"):
    dx, dy = _DXY[d]
    ex, ey = round(x + dx * length, 4), round(y + dy * length, 4)
    wire(p, x, y, ex, ey)
    label(p, text, ex, ey, kind=kind)
    return ex, ey


def probe_r(p, ref, x, y, net):
    place(p, "Device:R", ref, x, y + 3.81)
    stub(p, x, y, "up", net)


def triplicate(p):
    """Copy fixture p byte-identically into <dir>/<design> for every design, same file name."""
    d, fn = os.path.split(p)
    out = {}
    for k in rlib.DESIGNS:
        sub = os.path.join(d, k)
        os.makedirs(sub, exist_ok=True)
        q = os.path.join(sub, fn)
        shutil.copy2(p, q)
        out[k] = q
    return out


def run(fn, kw):
    try:
        return "OK", str(fn(**kw))
    except Exception as e:  # noqa: BLE001
        kind = type(e).__name__
        return ("REFUSED" if kind == "ToolError" else "ERROR"), f"{kind}: {e}"


def judge(tag, impl, p, fn, kw, targets=(), N=None, pair=None, files=None, board_refs=None,
          extra_allowed=(), note="", vid=None, stepno=1, nsteps=1, tool=None, edited=None):
    if vid is None:
        raise ValueError(f"judge() needs vid= ({tag})")
    if not rlib.wanted(vid):
        return None
    if tool is None:
        tool = next(t for k, t in _TOOL.items() if IMPLS[impl][k] is fn)
    return rlib.exec_design(impl, vid=vid, step=stepno, tool=tool, kw=kw, root=p,
                            edited=edited or kw["schematic_path"], note=tag, nsteps=nsteps)


def W(p, pins, N, **kw):
    return dict(pins=[{"reference": r, "pin": n} for r, n in pins], label_text=N, schematic_path=p,
                **kw)


def C(p, r1, n1, r2, n2):
    return dict(ref1=r1, pin1=n1, ref2=r2, pin2=n2, schematic_path=p)


def dump(name):
    rlib.flush()


EFF = "(effects (font (size 1.27 1.27)))"


def lpin(num, name, typ="passive", x=0.0, y=0.0, ang=0, length=2.54, hidden=False):
    h = " (hide yes)" if hidden else ""
    return (f'(pin {typ} line (at {_n(x)} {_n(y)} {ang}) (length {_n(length)}){h} '
            f'(name "{name}" {EFF}) (number "{num}" {EFF}))')


def lsym(name, units, ref="U"):
    props = (f'(property "Reference" "{ref}" (at 0 7.62 0) {EFF}) '
             f'(property "Value" "{name}" (at 0 -7.62 0) {EFF}) '
             f'(property "Footprint" "" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes))) '
             f'(property "Datasheet" "" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes))) ')
    body = ""
    for (u, s), pins in sorted(units.items()):
        rect = ('(rectangle (start -2.54 2.54) (end 2.54 -2.54) (stroke (width 0) (type default)) '
                '(fill (type none)))') if u != 0 else ""
        body += f'(symbol "{name}_{u}_{s}" {rect} {" ".join(pins)}) '
    return (f'(symbol "{name}" (exclude_from_sim no) (in_bom yes) (on_board yes) '
            f'{props}{body}(embedded_fonts no))')


def write_lib(fname, syms):
    path = os.path.join(LIBS, fname)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write('(kicad_symbol_lib (version 20241209) (generator "kicad_symbol_editor") '
                '(generator_version "9.0")\n' + "\n".join(syms) + "\n)\n")
    return path


def place_custom(p, lib_path, name, ref, x, y, rot=0):
    return schematic.place_component(lib_id=f"T:{name}", reference=ref, value=ref, x=x, y=y,
                                     rotation=rot, symbol_lib_path=lib_path, schematic_path=p)
