"""probeA rerun, category pin-identity: every scenario of caseidx/pin-identity.json (probe scripts
and verifier scripts) rebuilt from the originals' fixture code and run on design M2 and design K
through rlib (judge3: kicad-cli = measured, GUI-load proxy not available in this repository).

Fixture code is copied from probe-pin-identity/*.py (helpers pil.py, harness.py) and
verify-probe-pin-identity/*.py (helper vlib.py); every output path goes to rlib's per-job
scratch. Custom symbol libraries are written per job into rlib.newdir("libs").

Demo-sheet calls come from demo_calls.json (pi_prep.py: the originals' own selection logic) and
run on UNMODIFIED copies of the stock demo projects (*.kicad_sch and *.kicad_pro only) from the
installed KiCad's demos directory (hpaths.KICAD_DEMOS), the edited sheet being the tool target and
the project root the judged file (the verifier's approach).

Speed-only workarounds (no effect on any reading or fixture), installed in every worker process:
- judge3.gui_load_proxy is wrapped with a cache keyed on the input file's bytes. The proxy reads
  only its input file and its output differs between runs only in fresh wire uuids, so the cached
  output is netlist-identical. (Never reached here: the gui reading is not available in this
  repository, see core/gui_reading.py.)
- schematic._copy_lib_symbol_from_file_cst keeps each parsed symbol library (keyed on path, mtime,
  size) instead of re-parsing it per call; it copies out the same node bytes. It calls the
  package's _refuse_derived on the matched node first when the package has one, as the real
  function does.
- judge3.nets_full is cached on the bytes of the root's whole directory tree (same tree, same
  kicad-cli netlist); failed exports are not cached.
--resume skips jobs whose records are already complete in the records file (each job is written
in one block when it finishes, so a job is either complete there or absent).

Run: uv run python tools/routing_harness/suites/probeA/pin-identity/pi_jobs.py [--workers N]
     [--only SUBSTR ...] [--out PATH] [--list]
"""

from __future__ import annotations

import hashlib
import json
import shutil
import uuid
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)  # puts suites/probeA on sys.path, for rlib

import compat54  # noqa: E402  (frozen 54160c1 helpers: repo_pos below)
import rlib  # noqa: E402
from rlib import H, J, SCH, Job, _cst, cp, wptn  # noqa: E402
from rlib import PROJ  # noqa: E402

HERE = Path(__file__).resolve().parent
STOCK = hpaths.KICAD_DEMOS
CAT = "pin-identity"
R = "Device:R"
QBCE = "Transistor_BJT:Q_NPN_BCE"
PP = "probe-pin-identity"
VP = "verify-probe-pin-identity"
CASE_ORDER = [f"PI-{i:02d}" for i in range(1, 23)]

# ------------------------------------------------------------------ GUI proxy cache (speed only)
_PROXY: dict = {}
_orig_proxy = J.gui_load_proxy


def _cached_proxy(src, dst):
    data = Path(src).read_bytes()
    k = hashlib.sha256(data).hexdigest()
    out = _PROXY.get(k)
    if out is None:
        _orig_proxy(src, dst)
        _PROXY[k] = Path(dst).read_bytes()
    else:
        Path(dst).write_bytes(out)


J.gui_load_proxy = _cached_proxy

# ------------------------------------------------------------------ symbol-library parse cache (speed only)
# The repo's _copy_lib_symbol_from_file_cst re-parses the whole .kicad_sym on every call (judge3's
# probe parses the 5 MB Connector library three times per job; place_component parses Device).
# Same body, with the parsed library kept per (path, mtime, size); the node copied out is a deep
# copy, so the cached tree is never mutated and the bytes spliced are identical.
_LIBS: dict = {}


def _cached_copy_lib(root, lib_path, symbol_name, new_name):
    st = Path(lib_path).stat()
    key = (str(Path(lib_path).resolve()), st.st_mtime_ns, st.st_size)
    lib_root = _LIBS.get(key)
    if lib_root is None:
        lib_root = _LIBS[key] = _cst.parse(Path(lib_path).read_bytes()).lists[0]
    for s in lib_root.find_all("symbol"):
        if s.atoms[1].text == symbol_name:
            refuse = getattr(SCH, "_refuse_derived", None)  # the package's own check, where it has one
            if refuse is not None:
                refuse(s, new_name)
            node = s.copy()
            node.atoms[1].set_text(new_name)
            SCH._splice_lib_symbol_cst(root, node)
            return True
    return False


SCH._copy_lib_symbol_from_file_cst = _cached_copy_lib

# ------------------------------------------------------------------ netlist cache (speed only)
# judge3.nets_full(root) is a function of the bytes of the root's directory tree (the root, every
# sheet it references, the .kicad_pro): the same tree content gives the same kicad-cli netlist.
# Keyed on the root's name plus every file's relative path and sha256 in that tree. A failed
# export (None) is never cached, so a transient kicad-cli failure cannot repeat from the cache.
_NETS: dict = {}
_orig_nets_full = J.nets_full


def _cached_nets_full(path):
    path = Path(path)
    h = hashlib.sha256(path.name.encode() + b"\0")
    for f in sorted(path.parent.rglob("*")):
        if f.is_file():
            h.update(str(f.relative_to(path.parent)).replace("\\", "/").encode() + b"\0"
                     + hashlib.sha256(f.read_bytes()).digest())
    k = h.hexdigest()
    hit = _NETS.get(k)
    if hit is not None:
        return hit
    res = _orig_nets_full(path)
    if res is not None:
        _NETS[k] = res
    return res


J.nets_full = _cached_nets_full


# ------------------------------------------------------------------ helpers (pil.py / vlib.py)


def cs(*ids):
    return sorted(set(ids), key=CASE_ORDER.index)


def place(p, lib, ref, x, y, rot=0, mirror="", value=None):
    return H.place(p, lib, ref, x, y, rot=rot, mirror=mirror, value=value)


def kpos(p, ref, num, i=0):
    """KiCad-true pin end (m2_impl.pin_positions, KiCad's rotate-then-mirror transform)."""
    return rlib.pos(p, ref, num, i)


def repo_pos(p, ref, num):
    """The old repo's _get_pin_pos_cst point (frozen in core/compat54.py at the commit the baselines
    were taken on; the package's own function has since changed meaning): used ONLY as a
    deliberate phantom location."""
    root = _cst.parse(open(p, "rb").read()).lists[0]
    x, y, _o = compat54._get_pin_pos_cst(root, ref, num)
    return round(x, 4), round(y, 4)


EFF = "(effects (font (size 1.27 1.27)))"


def _n(v):
    s = f"{round(v, 4):.4f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def PIN(num, name="~", typ="passive", x=0.0, y=0.0, ang=0, length=2.54, hide=False, extra=""):
    h = " (hide yes)" if hide else ""
    return (f'(pin {typ} line (at {_n(x)} {_n(y)} {ang}) (length {_n(length)}){h} '
            f'(name "{name}" {EFF}) (number "{num}" {EFF}){extra})')


def SYM(name, subs, head_extra="", ref="U"):
    props = (f'(property "Reference" "{ref}" (at 0 10.16 0) {EFF}) '
             f'(property "Value" "{name}" (at 0 -10.16 0) {EFF}) '
             f'(property "Footprint" "" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes))) '
             f'(property "Datasheet" "" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes))) ')
    body = "".join(f'(symbol "{name}_{u}_{s}" {" ".join(pins)}) ' for (u, s), pins in sorted(subs.items()))
    return (f'(symbol "{name}" {head_extra} (exclude_from_sim no) (in_bom yes) (on_board yes) '
            f'{props}{body}(embedded_fonts no))')


def LIB(fname, syms):
    """pil.LIB, but into this job's own scratch libs dir."""
    path = rlib.newdir("libs") / fname
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write('(kicad_symbol_lib (version 20241209) (generator "kicad_symbol_editor") '
                '(generator_version "9.0")\n' + "\n".join(syms) + "\n)\n")
    return str(path)


def put(p, libpath, name, ref, x, y, rot=0, mirror="", value=None):
    return SCH.place_component(lib_id=f"PI:{name}", reference=ref, value=value or ref, x=x, y=y,
                               rotation=rot, mirror=mirror, symbol_lib_path=libpath, schematic_path=p)


def vfresh(tag):
    """vlib.fresh naming: tag plus a 6-hex suffix (file stem differs from any instance project)."""
    return rlib.fresh(f"{tag}_{uuid.uuid4().hex[:6]}")


def _sheet(name, x, w, ru, page, proj, su):
    return _cst.parse((
        f'(sheet (at {x} 50.8) (size {w} 20.32) (exclude_from_sim no) (in_bom yes) (on_board yes)'
        f' (dnp no) (fields_autoplaced yes) (stroke (width 0.1524) (type solid))'
        f' (fill (color 0 0 0 0.0000)) (uuid "{su}")'
        f' (property "Sheetname" "{name}" (at {x} 50.0884 0) (effects (font (size 1.27 1.27))'
        f' (justify left bottom)))'
        f' (property "Sheetfile" "chan.kicad_sch" (at {x} 71.7046 0) (effects (font (size 1.27 1.27))'
        f' (justify left top)))'
        f' (instances (project "{proj}" (path "/{ru}" (page "{page}")))))').encode()).lists[0]


def _reused(proj, width):
    """Root with sheets S1 (x 50.8) and S2 (x 101.6) both on chan.kicad_sch; returns paths, ids."""
    d = rlib.newdir("reuse") / proj
    d.mkdir()
    (d / f"{proj}.kicad_pro").write_text(f'{{"meta": {{"filename": "{proj}.kicad_pro", "version": 1}}}}\n')
    root_p, chan_p = str(d / f"{proj}.kicad_sch"), str(d / "chan.kicad_sch")
    PROJ.create_schematic(schematic_path=root_p)
    PROJ.create_schematic(schematic_path=chan_p)
    ru = _cst.parse(open(root_p, "rb").read()).lists[0].find("uuid").atoms[1].text
    sids = []
    for k, (name, x) in enumerate((("S1", 50.8), ("S2", 101.6))):
        su = str(uuid.uuid4())
        sids.append(su)
        node = _sheet(name, x, width, ru, k + 2, proj, su)
        H._edit(root_p, lambda r, node=node: SCH._splice_sch_node(r, "sheet", node))
    return root_p, chan_p, ru, sids


def set_instances(p, ref, top, entries):
    """vlib-era v04 helper: entries [(project, path or None for '/<root uuid>', unit)]."""
    def fn(root):
        ru = root.find("uuid").atoms[1].text
        s = SCH._find_syms_cst(root, ref)[0]
        s.find("unit").atoms[1].set_text(str(top))
        body = " ".join(f'(project "{pj}" (path "{pth or "/" + ru}" (reference "{ref}") (unit {u})))'
                        for pj, pth, u in entries)
        new = _cst.parse(f"(instances {body})".encode()).lists[0]
        old = s.find("instances")
        s.insert_after(old, new)
        s.remove_child(old)
    H._edit(p, fn)


def clone_unit2(p, ref, x):
    """pad_alt_order.py / v08: clone the first placed node of ref as unit 2 at (x, same y)."""
    def fn(root):
        src = SCH._find_syms_cst(root, ref)[0]
        node = src.copy()
        node.find("at").atoms[1].set_text(x)
        node.find("unit").atoms[1].set_text("2")
        node.find("uuid").atoms[1].set_text(str(uuid.uuid4()))
        for pn in node.find_all("pin"):
            u = pn.find("uuid")
            if u is not None:
                u.atoms[1].set_text(str(uuid.uuid4()))
        node.find("instances").find("project").find("path").find("unit").atoms[1].set_text("2")
        for prop in node.find_all("property"):
            prop.find("at").atoms[1].set_text(x)
        root.insert_after(src, node)
    H._edit(p, fn)


def select_alt(p, ref, alt):
    def fn(root):
        s = SCH._find_syms_cst(root, ref)[0]
        for pn in s.find_all("pin"):
            if pn.atoms[1].text == "1":
                pn.append_child(_cst.parse(f'(alternate "{alt}")'.encode()).lists[0], b" ")
    H._edit(p, fn)


def mirror_first(p, ref, axis):
    def fn(root):
        s = SCH._find_syms_cst(root, ref)[0]
        s.insert_before(s.find("at"), _cst.parse(f"(mirror {axis})".encode()).lists[0])
    H._edit(p, fn)


def rename_refs(p, olds, dup):
    def fn(root):
        for old in olds:
            s = SCH._find_syms_cst(root, old)[0]
            for pr in s.find_all("property"):
                if pr.atoms[1].text == "Reference":
                    pr.atoms[2].set_text(dup)
            s.find("instances").find("project").find("path").find("reference").atoms[1].set_text(dup)
    H._edit(p, fn)


def set_lib_name(p, ref, name):
    H._edit(p, lambda r: SCH._find_syms_cst(r, ref)[0].find("lib_name").atoms[1].set_text(name))


# ------------------------------------------------------------------ builders: o12 (PI-01/12/22)


def b_o12(lib, ref, rot, mir, with_r9):
    p = rlib.fresh("o12")
    place(p, lib, ref, 101.6, 101.6, rot=rot, mirror=mir)
    if with_r9:
        place(p, R, "R9", 152.4, 152.4)
    return p


# ------------------------------------------------------------------ demo projects (PI-02/04/05/22)
DEMO = {"FH": ("flat_hierarchy", "pic_programmer.kicad_sch", "flat_hierarchy.kicad_sch"),
        "Q17": ("simulation/q17", "Q17ng.kicad_sch", "Q17ng.kicad_sch"),
        "VME": ("vme-wren", "vme_buffers_addr.kicad_sch", "vme-wren.kicad_sch")}


def b_demo(key):
    rel, sheet, rootf = DEMO[key]
    d = rlib.newdir(key) / Path(rel).name
    d.mkdir()
    for f in (STOCK / rel).iterdir():
        if f.is_file() and f.suffix in (".kicad_sch", ".kicad_pro"):
            shutil.copy2(f, d / f.name)
    return str(d / rootf), str(d / sheet)


# ------------------------------------------------------------------ neighbour_phantom / v03 (PI-03/04/05)


def b_np_2a():
    p = rlib.fresh("np_2a")
    place(p, R, "R1", 101.6, 101.6)
    place(p, QBCE, "Q1", 96.52, 92.71, rot=90, mirror="x")
    assert kpos(p, "Q1", "3") == (101.6, 95.25), kpos(p, "Q1", "3")
    return p


def b_np_2b():
    lib = LIB("pi_hpwr.kicad_sym", [SYM("HPWR", {(1, 1): [
        PIN("1", "IN", "passive", -5.08, 0, 0),
        PIN("2", "VCC", "power_in", 2.54, 5.08, 270, hide=True)]})])
    p = rlib.fresh("np_2b")
    place(p, R, "R1", 101.6, 101.6)
    put(p, lib, "HPWR", "U1", 106.68, 92.71, rot=90, mirror="x")
    place(p, "power:VCC", "#PWR01", 152.4, 101.6, value="VCC")
    place(p, R, "R5", 152.4, 105.41)
    assert kpos(p, "U1", "2") == (101.6, 95.25), kpos(p, "U1", "2")
    return p


def b_np_2cd(text):
    p = rlib.fresh("np_2cd")
    place(p, QBCE, "Q1", 127, 101.6, rot=90, mirror="x")
    bx, by = repo_pos(p, "Q1", "1")  # phantom: the repo's point for Q1:B (deliberate)
    assert (bx, by) != kpos(p, "Q1", "1")
    place(p, R, "R2", 127, 116.84)
    H.wire(p, bx, by, 127, 113.03)
    H.label(p, text, bx, by)
    return p


HV_TEXT = (
    '(kicad_symbol_lib (version 20241209) (generator "kicad_symbol_editor") (generator_version "9.0")\n'
    ' (symbol "HVCC" (exclude_from_sim no) (in_bom yes) (on_board yes)\n'
    f'  (property "Reference" "U" (at 0 7.62 0) {EFF})\n'
    f'  (property "Value" "HVCC" (at 0 -7.62 0) {EFF})\n'
    '  (property "Footprint" "" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))\n'
    '  (property "Datasheet" "" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))\n'
    '  (symbol "HVCC_0_1" (rectangle (start -2.54 2.54) (end 2.54 -2.54)'
    ' (stroke (width 0.254) (type default)) (fill (type background))))\n'
    '  (symbol "HVCC_1_1"\n'
    f'   (pin passive line (at -5.08 0 0) (length 2.54) (name "IN" {EFF}) (number "1" {EFF}))\n'
    f'   (pin power_in line (at 2.54 5.08 270) (length 2.54) (hide yes) (name "VCC" {EFF}) (number "2" {EFF})))\n'
    '  (embedded_fonts no))\n)\n')


def b_v03_A():
    p = vfresh("phA")
    place(p, R, "R1", 127, 127)
    place(p, QBCE, "Q1", 121.92, 118.11, rot=270, mirror="y")
    assert kpos(p, "Q1", "3") == (127.0, 120.65), kpos(p, "Q1", "3")
    assert kpos(p, "R1", "1") == (127.0, 123.19)
    return p


def b_v03_Bh():
    hv = rlib.newdir("libs") / "vhv.kicad_sym"
    hv.write_text(HV_TEXT, encoding="utf-8")
    p = vfresh("phB")
    place(p, R, "R1", 127, 127)
    SCH.place_component(lib_id="VHV:HVCC", reference="U1", value="HVCC", x=127 + 5.08, y=120.65 - 2.54,
                        rotation=270, mirror="y", symbol_lib_path=str(hv), schematic_path=p)
    place(p, "power:VCC", "#PWR01", 177.8, 127, value="VCC")
    place(p, R, "R5", 177.8, 130.81)
    assert kpos(p, "U1", "2") == (127.0, 120.65), kpos(p, "U1", "2")
    return p


def b_v03_CD(text):
    p = vfresh(f"ph{text}")
    place(p, QBCE, "Q1", 152.4, 101.6, rot=270, mirror="y")
    bx, by = repo_pos(p, "Q1", "1")  # phantom: the repo's point for Q1:B (deliberate)
    assert (bx, by) != kpos(p, "Q1", "1")
    place(p, R, "R2", bx + 12.7, by + 3.81)
    H.wire(p, bx, by, bx + 12.7, by)
    H.label(p, text, bx, by)
    return p


# ------------------------------------------------------------------ units (PI-06/16/22)


def b_ui(top, inst):
    p = rlib.fresh("ui")
    place(p, "74xx:74LS00", "U1", 101.6, 101.6)
    place(p, R, "R9", 152.4, 152.4)

    def fn(root):
        s = SCH._find_syms_cst(root, "U1")[0]
        s.find("unit").atoms[1].set_text(str(top))
        s.find("instances").find("project").find("path").find("unit").atoms[1].set_text(str(inst))
    H._edit(p, fn)
    return p


def b_ui_4b():
    root_p, chan_p, ru, sids = _reused("proj", 30.48)
    place(chan_p, "74xx:74LS00", "U1", 101.6, 101.6)
    inst = _cst.parse(
        (f'(instances (project "proj" (path "/{ru}/{sids[0]}" (reference "U1") (unit 1))'
         f' (path "/{ru}/{sids[1]}" (reference "U1") (unit 2))))').encode()).lists[0]

    def fix(r):
        s = SCH._find_syms_cst(r, "U1")[0]
        old = s.find("instances")
        s.insert_after(old, inst)
        s.remove_child(old)
    H._edit(chan_p, fix)
    return root_p, chan_p


def b_v04_s1():
    p = vfresh("s1")
    place(p, "74xx:74LS00", "U1", 101.6, 101.6)
    place(p, R, "R9", 177.8, 177.8)
    set_instances(p, "U1", 3, [("s1", None, 4)])
    return p


def b_v04_rs():
    root_p, chan_p, ru, sids = _reused("rsproj", 25.4)
    place(chan_p, "74xx:74LS00", "U1", 101.6, 101.6)
    set_instances(chan_p, "U1", 3, [("rsproj", f"/{ru}/{sids[0]}", 3), ("rsproj", f"/{ru}/{sids[1]}", 4)])
    return root_p, chan_p


def b_v04_mp():
    p = vfresh("mp")
    place(p, "74xx:74LS00", "U1", 101.6, 101.6)
    set_instances(p, "U1", 3, [("mp", None, 3), ("other", "/00000000-0000-0000-0000-00000000beef", 4)])
    return p


def b_me_12():
    p = rlib.fresh("me_12")
    place(p, "74xx:74LS00", "U1", 101.6, 101.6)

    def fn(root):
        inst = SCH._find_syms_cst(root, "U1")[0].find("instances")
        extra = _cst.parse(b'(project "other" (path "/00000000-0000-0000-0000-00000000beef"'
                           b' (reference "U1") (unit 2)))').lists[0]
        inst.append_child(extra, b" ")
    H._edit(p, fn)
    return p


# ------------------------------------------------------------------ duplicate references (PI-15)


def b_dup(lib, a, b, dup):
    p = rlib.fresh("dup")
    place(p, lib, a, 101.6, 101.6)
    place(p, lib, b, 127, 101.6)
    place(p, R, "R7", 93.98, 97.79, rot=90)  # R7:2 at (97.79, 97.79)
    H.wire(p, 97.79, 97.79, 101.6, 97.79)
    place(p, R, "R8", 119.38, 97.79, rot=90)  # R8:2 at (123.19, 97.79)
    H.wire(p, 123.19, 97.79, 127, 97.79)
    rename_refs(p, (a, b), dup)
    return p


# ------------------------------------------------------------------ lib_name (PI-07/08/17/22)


def _lib_entry(root, name):
    for s in root.find("lib_symbols").find_all("symbol"):
        if s.atoms[1].text == name:
            return s
    raise KeyError(name)


def _variant(root, src_name, new_name, old_pre, new_pre, swap=False):
    """libname.py variant(): copy lib entry src_name as new_name, optionally swap pins 1/2 (at)."""
    node = _lib_entry(root, src_name).copy()
    node.atoms[1].set_text(new_name)
    for sub in node.find_all("symbol"):
        t = sub.atoms[1].text
        assert t.startswith(old_pre + "_"), t
        sub.atoms[1].set_text(new_pre + t[len(old_pre):])
        if swap:
            pins = {pn.find("number").atoms[1].text: pn for pn in sub.find_all("pin")}
            if "1" in pins and "2" in pins:
                a1, a2 = pins["1"].find("at"), pins["2"].find("at")
                v1 = [a.text for a in a1.atoms[1:]]
                v2 = [a.text for a in a2.atoms[1:]]
                for a, v in zip(a1.atoms[1:], v2):
                    a.set_text(v)
                for a, v in zip(a2.atoms[1:], v1):
                    a.set_text(v)
    libs = root.find("lib_symbols")
    libs.insert_after(libs.find_all("symbol")[-1], node)


def b_ln_5a():
    p = rlib.fresh("ln_5a")
    place(p, "Device:D", "D2", 101.6, 101.6)
    place(p, R, "R9", 152.4, 152.4)

    def fn(r):
        _variant(r, "Device:D", "D_1", "D", "D_1", swap=True)
        SCH._find_syms_cst(r, "D2")[0].find("lib_name").atoms[1].set_text("D_1")
    H._edit(p, fn)
    return p


def b_ln_5b():
    p = rlib.fresh("ln_5b")
    place(p, R, "R1", 101.6, 101.6)
    place(p, "Device:D", "D2", 105.41, 95.25)

    def fn(r):
        _variant(r, "Device:D", "D_1", "D", "D_1")
        SCH._find_syms_cst(r, "D2")[0].find("lib_name").atoms[1].set_text("D_1")
        r.find("lib_symbols").remove_child(_lib_entry(r, "Device:D"))
    H._edit(p, fn)
    assert kpos(p, "D2", "1") == (101.6, 95.25), kpos(p, "D2", "1")
    return p


def b_ln_5c():
    p = rlib.fresh("ln_5c")
    place(p, R, "R1", 76.2, 101.6)
    place(p, R, "R2", 101.6, 101.6)
    place(p, R, "R9", 152.4, 152.4)

    def fn(r):
        _variant(r, "Device:R", "MyLib:R", "R", "R", swap=True)
        s = SCH._find_syms_cst(r, "R2")[0]
        s.find("lib_id").atoms[1].set_text("MyLib:R")
        s.remove_child(s.find("lib_name"))
    H._edit(p, fn)
    return p


def b_ln_5d():
    p = rlib.fresh("ln_5d")
    place(p, "Device:D", "D3", 101.6, 101.6)
    place(p, R, "R9", 152.4, 152.4)
    set_lib_name(p, "D3", "D_9")
    return p


def b_m_orphan():
    p = rlib.fresh("mo")
    place(p, R, "R1", 101.6, 101.6)
    place(p, "Device:D", "D3", 101.6, 95.25)
    set_lib_name(p, "D3", "D_9")
    return p


CPOL = "Device:C_Polarized"


def _add_cpol_variant(root, new_name, swap):
    """v05_libname.py add_variant()."""
    node = _lib_entry(root, CPOL).copy()
    node.atoms[1].set_text(new_name)
    for sub in node.find_all("symbol"):
        t = sub.atoms[1].text
        sub.atoms[1].set_text(new_name.split(":")[-1] + t[len("C_Polarized"):])
        if swap:
            pins = {pn.find("number").atoms[1].text: pn.find("at") for pn in sub.find_all("pin")}
            if "1" in pins and "2" in pins:
                v1 = [a.text for a in pins["1"].atoms[1:]]
                v2 = [a.text for a in pins["2"].atoms[1:]]
                for a, v in zip(pins["1"].atoms[1:], v2):
                    a.set_text(v)
                for a, v in zip(pins["2"].atoms[1:], v1):
                    a.set_text(v)
    libs = root.find("lib_symbols")
    libs.insert_after(libs.find_all("symbol")[-1], node)


def b_v05_La():
    p = vfresh("La")
    place(p, CPOL, "C1", 101.6, 101.6)
    place(p, R, "R9", 177.8, 177.8)

    def fn(r):
        _add_cpol_variant(r, "C_Polarized_1", True)
        SCH._find_syms_cst(r, "C1")[0].find("lib_name").atoms[1].set_text("C_Polarized_1")
    H._edit(p, fn)
    return p


def b_v05_Lb():
    p = vfresh("Lb")
    place(p, R, "R1", 101.6, 101.6)
    place(p, CPOL, "C1", 101.6, 95.25 - 3.81, rot=0)

    def fn(r):
        _add_cpol_variant(r, "C_Polarized_1", False)
        SCH._find_syms_cst(r, "C1")[0].find("lib_name").atoms[1].set_text("C_Polarized_1")
        r.find("lib_symbols").remove_child(_lib_entry(r, CPOL))
    H._edit(p, fn)
    pts = [tuple(x[:2]) for v in rlib.M2.pin_positions(p, "C1").values() for x in v]
    assert (101.6, 95.25) in pts, pts
    return p


def b_v05_Lc():
    p = vfresh("Lc")
    place(p, CPOL, "C1", 101.6, 101.6)

    def fn(r):
        _add_cpol_variant(r, "MyLib:C_Polarized", True)
        s = SCH._find_syms_cst(r, "C1")[0]
        s.find("lib_id").atoms[1].set_text("MyLib:C_Polarized")
        s.remove_child(s.find("lib_name"))
    H._edit(p, fn)
    return p


def b_v05_Ld():
    p = vfresh("Ld")
    place(p, R, "R1", 101.6, 101.6)
    place(p, CPOL, "C1", 101.6, 95.25)  # origin at R1:1's 1x stub end
    set_lib_name(p, "C1", "C_Polarized_9")
    return p


# ------------------------------------------------------------------ pin arguments (PI-09/14/18/19)


def b_pnn(lib):
    p = rlib.fresh("pnn")
    place(p, lib, "U1", 101.6, 101.6)
    place(p, R, "R9", 177.8, 177.8)
    return p


def b_v06(lib):
    p = vfresh("pn")
    place(p, lib, "U1", 127, 101.6)
    place(p, R, "R9", 266.7, 190.5)
    return p


def b_v06d():
    p = vfresh("st3")
    place(p, "MCU_ST_STM32F1:STM32F100C_4-6_Tx", "U1", 127, 101.6)
    return p


# ------------------------------------------------------------------ nc / hidden / alternates (PI-10/19/20/21)


def b_nch_7a():
    p = rlib.fresh("nch_7a")
    place(p, "Connector:AVR-TPI-6", "J1", 101.6, 101.6)
    place(p, R, "R9", 152.4, 152.4)
    return p


def b_nch_7b():
    p = rlib.fresh("nch_7b")
    place(p, "Connector:AVR-TPI-6", "J1", 101.6, 101.6)
    x, y = kpos(p, "J1", "4")
    place(p, R, "R5", x, y + 11.43)  # R5:1 at (x, y + 7.62)
    H.wire(p, x, y, x, y + 7.62)
    place(p, R, "R6", 152.4, 101.6)  # R6:1 at (152.4, 97.79)
    H.label(p, "N4", 152.4, 97.79)
    return p


def b_nch_8a():
    p = rlib.fresh("nch_8a")
    place(p, "Amplifier_Current:INA240A1D", "U1", 101.6, 101.6)
    return p


def b_nch_8bc(part):
    lib = LIB("pi_hidden.kicad_sym", [
        SYM("HPAS", {(1, 1): [PIN("1", "IN", "passive", -5.08, 0, 0),
                              PIN("2", "AUX", "passive", 2.54, 5.08, 270, hide=True)]}),
        SYM("HPWR", {(1, 1): [PIN("1", "IN", "passive", -5.08, 0, 0),
                              PIN("2", "VCC", "power_in", 2.54, 5.08, 270, hide=True)]}),
    ])
    p = rlib.fresh(f"nch_{part}")
    place(p, R, "R1", 101.6, 101.6)  # 1x stub end (101.6, 95.25)
    put(p, lib, part, "U1", 99.06, 100.33)  # hidden pin 2 end at (101.6, 95.25)
    if part == "HPWR":
        place(p, "power:VCC", "#PWR01", 152.4, 101.6, value="VCC")
        place(p, R, "R5", 152.4, 105.41)
    assert kpos(p, "U1", "2") == (101.6, 95.25), kpos(p, "U1", "2")
    return p


def b_pao_9():
    p = rlib.fresh("pao_9")
    place(p, "Device:R_Network04_Split", "RN1", 101.6, 101.6)
    place(p, R, "R9", 177.8, 177.8)
    clone_unit2(p, "RN1", "127")
    return p


def b_pao_10(alt):
    lib = LIB("pi_alt.kicad_sym", [SYM("ALTP", {(1, 1): [
        PIN("1", "IO", "passive", -5.08, 0, 0,
            extra=' (alternate "NCX" no_connect line) (alternate "SDA" bidirectional line)'),
        PIN("2", "GND", "passive", 5.08, 0, 180)]})])
    p = rlib.fresh(f"pao_10_{alt}")
    put(p, lib, "ALTP", "U1", 101.6, 101.6)
    select_alt(p, "U1", alt)
    return p


def b_pao_11():
    p = rlib.fresh("pao_11")
    place(p, R, "R1", 101.6, 101.6)
    mirror_first(p, "R1", "x")
    return p


def b_v07_n13(lib, ref):
    p = vfresh("n1")
    place(p, lib, ref, 101.6, 101.6)
    place(p, R, "R9", 177.8, 177.8)
    return p


def b_v07_n2(lib, ref, num):
    p = vfresh("n2")
    place(p, lib, ref, 101.6, 101.6)
    ex, ey = kpos(p, ref, num)
    place(p, R, "R5", ex, ey + 25.4 + 3.81)  # R5:1 at (ex, ey + 25.4)
    H.wire(p, ex, ey, ex, ey + 25.4)
    place(p, R, "R6", 177.8, 101.6)
    H.label(p, "N4", 177.8, 97.79)
    return p


VALT_TEXT = (
    '(kicad_symbol_lib (version 20241209) (generator "kicad_symbol_editor") (generator_version "9.0")\n'
    ' (symbol "ALTX" (exclude_from_sim no) (in_bom yes) (on_board yes)\n'
    f'  (property "Reference" "U" (at 0 7.62 0) {EFF})\n'
    f'  (property "Value" "ALTX" (at 0 -7.62 0) {EFF})\n'
    '  (property "Footprint" "" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))\n'
    '  (property "Datasheet" "" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))\n'
    '  (symbol "ALTX_1_1"\n'
    f'   (pin passive line (at -7.62 0 0) (length 2.54) (name "IO" {EFF}) (number "1" {EFF})'
    ' (alternate "NCX" no_connect line) (alternate "SDA" bidirectional line))\n'
    f'   (pin passive line (at 7.62 0 180) (length 2.54) (name "GND" {EFF}) (number "2" {EFF})))\n'
    '  (embedded_fonts no))\n)\n')


def b_v07_n4(alt):
    lib = rlib.newdir("libs") / "valt.kicad_sym"
    lib.write_text(VALT_TEXT, encoding="utf-8")
    p = vfresh(f"n4{alt}")
    SCH.place_component(lib_id="VALT:ALTX", reference="U1", value="ALTX", x=101.6, y=101.6,
                        symbol_lib_path=str(lib), schematic_path=p)
    select_alt(p, "U1", alt)
    return p


def b_v08():
    p = vfresh("pc")
    place(p, "Device:D_Dual_CommonAnode_AKK_Split", "Q5", 101.6, 101.6)
    place(p, R, "R9", 203.2, 203.2)
    clone_unit2(p, "Q5", "152.4")
    return p


def b_v09():
    p = vfresh("mo")
    place(p, "Device:D", "D1", 101.6, 101.6, rot=180)
    mirror_first(p, "D1", "y")
    return p


# ------------------------------------------------------------------ the job list


def g(*nodes):
    return [set(nodes)]


def build_jobs():
    jobs: list = []

    def add(cases, variant, origin, source, build, args=(), steps=(), note=""):
        jobs.append(Job(cs(*cases), variant, origin, source, build, args, steps, note))

    # demo sheets first (the slow ones), VME before FH/Q17
    calls = json.load(open(HERE / "demo_calls.json"))
    calls.sort(key=lambda c: c["key"] != "VME")
    for c in calls:
        if c["kind"] == "wptn":
            cases = ("PI-02", "PI-05", "PI-22")
            sub = f"{c['key']}_{c['ref']}_{c['pin']}_{'A' if c['fresh'] else 'B'}"
            add(cases, f"kicad_fixture/{sub}", " | ".join(c["origins"]), "probe", b_demo, (c["key"],),
                [wptn([(c["ref"], c["pin"])], c["N"])],
                note="unmodified demo project copy; edited sheet = target, root judged")
        else:
            cases = ("PI-02", "PI-04", "PI-05", "PI-22")
            sub = f"{c['key']}_{c['a'][0]}_{c['a'][1]}-{c['b'][0]}_{c['b'][1]}"
            add(cases, f"kicad_fixture_connect/{sub}", " | ".join(c["origins"]), "probe", b_demo,
                (c["key"],), [cp(tuple(c["a"]), tuple(c["b"]))],
                note="unmodified demo project copy; edited sheet = target, root judged")

    # o12 sweep (v01_o12.py rebuilds the identical geometry, so its calls are these jobs)
    parts = [("Device:R", "R1", ["1", "2"]), ("Device:D", "D1", ["1", "2"]), (QBCE, "Q1", ["1", "2", "3"])]
    for lib, ref, pins in parts:
        for mir in ("", "x", "y"):
            for rot in (0, 90, 180, 270):
                for pn in pins:
                    tag = f"{ref}_r{rot}_m{mir or '-'}_p{pn}"
                    org = (f"{PP}/o12_sweep.py {ref} rot {rot} mirror {mir or '-'} pin {pn}"
                           f" (= {VP}/v01_o12.py, identical geometry; read by {PP}/o12_direction_check.py)")
                    add(("PI-01", "PI-12", "PI-22"), f"o12_sweep/{tag}_wptn", org + " wire NX", "probe",
                        b_o12, (lib, ref, rot, mir, False), [wptn([(ref, pn)], "NX")])
                    add(("PI-01", "PI-12", "PI-22"), f"o12_sweep/{tag}_cp", org + " connect R9:1", "probe",
                        b_o12, (lib, ref, rot, mir, True), [cp((ref, pn), ("R9", "1"))])

    # neighbour_phantom.py
    add(("PI-03",), "neighbour_phantom/2a", f"{PP}/neighbour_phantom.py 2a", "probe", b_np_2a,
        steps=[wptn([("R1", "1")], "NA")])
    add(("PI-03",), "neighbour_phantom/2b", f"{PP}/neighbour_phantom.py 2b", "probe", b_np_2b,
        steps=[wptn([("R1", "1")], "NA")])
    add(("PI-04",), "neighbour_phantom/2c", f"{PP}/neighbour_phantom.py 2c", "probe", b_np_2cd, ("NA",),
        [wptn([("Q1", "1")], "NA")], note="label at the repo (phantom) point of Q1:B, by design")
    add(("PI-05",), "neighbour_phantom/2d", f"{PP}/neighbour_phantom.py 2d", "probe", b_np_2cd, ("OTHER",),
        [wptn([("Q1", "1")], "NA")], note="label at the repo (phantom) point of Q1:B, by design")
    # v03_phantom.py
    add(("PI-03",), "v03_phantom/A", f"{VP}/v03_phantom.py A", "verify", b_v03_A,
        steps=[wptn([("R1", "1")], "NA")])
    add(("PI-03",), "v03_phantom/Bh", f"{VP}/v03_phantom.py Bh", "verify", b_v03_Bh,
        steps=[wptn([("R1", "1")], "NA")])
    add(("PI-04",), "v03_phantom/C", f"{VP}/v03_phantom.py C", "verify", b_v03_CD, ("NA",),
        [wptn([("Q1", "1")], "NA")], note="label at the repo (phantom) point of Q1:B, by design")
    add(("PI-05",), "v03_phantom/D", f"{VP}/v03_phantom.py D", "verify", b_v03_CD, ("OTHER",),
        [wptn([("Q1", "1")], "NA")], note="label at the repo (phantom) point of Q1:B, by design")

    # unit_instances.py
    for tag, top, inst in (("4a", 1, 2), ("4r", 2, 1)):
        for pn, N in (("1", "NA"), ("4", "NB")):
            add(("PI-06", "PI-22"), f"unit_instances/{tag}_wire_U1_{pn}",
                f"{PP}/unit_instances.py {tag} wire U1:{pn} {N}", "probe", b_ui, (top, inst),
                [wptn([("U1", pn)], N)])
            add(("PI-06", "PI-22"), f"unit_instances/{tag}_connect_U1_{pn}",
                f"{PP}/unit_instances.py {tag} connect U1:{pn} R9:1", "probe", b_ui, (top, inst),
                [cp(("U1", pn), ("R9", "1"))])
    add(("PI-06", "PI-22"), "unit_instances/4b_reused", f"{PP}/unit_instances.py 4b wire U1:1 NA (reused sheet)",
        "probe", b_ui_4b, steps=[wptn([("U1", "1")], "NA")], note="hierarchical: chan edited, root judged")
    # v04_units.py (KU a-d are label probes only: no tool call)
    for pn, N in (("9", "NA"), ("12", "NB")):
        add(("PI-06",), f"v04_units/S1_wire_U1_{pn}", f"{VP}/v04_units.py S1 wire U1:{pn} {N}", "verify",
            b_v04_s1, steps=[wptn([("U1", pn)], N)])
    add(("PI-06",), "v04_units/S1_connect_U1_9", f"{VP}/v04_units.py S1 connect U1:9 R9:1", "verify",
        b_v04_s1, steps=[cp(("U1", "9"), ("R9", "1"))])
    add(("PI-06",), "v04_units/RS", f"{VP}/v04_units.py RS wire U1:9 NA (reused sheet)", "verify",
        b_v04_rs, steps=[wptn([("U1", "9")], "NA")], note="hierarchical: chan edited, root judged")
    add(("PI-16",), "v04_units/MP", f"{VP}/v04_units.py MP wire U1:9 NA", "verify", b_v04_mp,
        steps=[wptn([("U1", "9")], "NA")])
    # m_edges.py
    add(("PI-16",), "m_edges/12", f"{PP}/m_edges.py 12 wire U1:1 NA", "probe", b_me_12,
        steps=[wptn([("U1", "1")], "NA")])
    for dup in ("R1", "R?"):
        add(("PI-15",), f"m_edges/13_{dup.replace('?', 'Q')}", f"{PP}/m_edges.py 13 duplicate {dup}", "probe",
            b_dup, (R, "R1", "R2", dup), [wptn([(dup, "1")], "NDUP")])
    # v10_dupref.py
    for dup in ("C1", "C?"):
        add(("PI-15",), f"v10_dupref/{dup.replace('?', 'Q')}", f"{VP}/v10_dupref.py DR {dup}", "verify",
            b_dup, ("Device:C", "C1", "C2", dup), [wptn([(dup, "1")], "NDUP")])

    # libname.py (demo_libname_unresolved.py is a read-only census: no tool call)
    add(("PI-07", "PI-22"), "libname/5a_wire_D2_1", f"{PP}/libname.py 5a wire D2:1 NK", "probe", b_ln_5a,
        steps=[wptn([("D2", "1")], "NK")])
    add(("PI-07", "PI-22"), "libname/5a_wire_D2_2", f"{PP}/libname.py 5a wire D2:2 NAN", "probe", b_ln_5a,
        steps=[wptn([("D2", "2")], "NAN")])
    add(("PI-07", "PI-22"), "libname/5a_connect_D2_1", f"{PP}/libname.py 5a connect D2:1 R9:1", "probe",
        b_ln_5a, steps=[cp(("D2", "1"), ("R9", "1"))])
    add(("PI-08", "PI-22"), "libname/5b_wire_D2_1", f"{PP}/libname.py 5b wire D2:1 NK", "probe", b_ln_5b,
        steps=[wptn([("D2", "1")], "NK")])
    add(("PI-08", "PI-22"), "libname/5b_wire_R1_1", f"{PP}/libname.py 5b wire R1:1 NA", "probe", b_ln_5b,
        steps=[wptn([("R1", "1")], "NA")])
    add(("PI-07", "PI-22"), "libname/5c_wire_R2_1", f"{PP}/libname.py 5c wire R2:1 N1", "probe", b_ln_5c,
        steps=[wptn([("R2", "1")], "N1")])
    add(("PI-07", "PI-22"), "libname/5c_connect_R2_1", f"{PP}/libname.py 5c connect R2:1 R9:1", "probe",
        b_ln_5c, steps=[cp(("R2", "1"), ("R9", "1"))])
    add(("PI-17", "PI-22"), "libname/5d_wire_D3_1", f"{PP}/libname.py 5d wire D3:1 NK", "probe", b_ln_5d,
        steps=[wptn([("D3", "1")], "NK")])
    add(("PI-17",), "m_orphan/R1_1", f"{PP}/m_orphan.py wire R1:1 NA (libname_unresolved_origin.py: label"
        " probes only, no tool call)", "probe", b_m_orphan, steps=[wptn([("R1", "1")], "NA")])
    # v05_libname.py
    add(("PI-07",), "v05_libname/La_wire", f"{VP}/v05_libname.py La wire C1:1 NP", "verify", b_v05_La,
        steps=[wptn([("C1", "1")], "NP")])
    add(("PI-07",), "v05_libname/La_connect", f"{VP}/v05_libname.py La connect C1:1 R9:1", "verify",
        b_v05_La, steps=[cp(("C1", "1"), ("R9", "1"))])
    add(("PI-08",), "v05_libname/Lb_target", f"{VP}/v05_libname.py Lb-target wire C1:1 NP", "verify",
        b_v05_Lb, steps=[wptn([("C1", "1")], "NP")])
    add(("PI-08",), "v05_libname/Lb_obstacle", f"{VP}/v05_libname.py Lb-obstacle wire R1:1 NA", "verify",
        b_v05_Lb, steps=[wptn([("R1", "1")], "NA")])
    add(("PI-07",), "v05_libname/Lc_wire", f"{VP}/v05_libname.py Lc wire C1:1 NP", "verify", b_v05_Lc,
        steps=[wptn([("C1", "1")], "NP")])
    add(("PI-17",), "v05_libname/Ld_target", f"{VP}/v05_libname.py Ld-target wire C1:1 NP", "verify",
        b_v05_Ld, steps=[wptn([("C1", "1")], "NP")])
    add(("PI-17",), "v05_libname/Ld_obstacle", f"{VP}/v05_libname.py Ld-obstacle wire R1:1 NA", "verify",
        b_v05_Ld, steps=[wptn([("R1", "1")], "NA")])

    # pin_name_number.py
    add(("PI-09", "PI-18"), "pin_name_number/6a_wire_1", f"{PP}/pin_name_number.py 6a wire U1:'1' N1", "probe",
        b_pnn, ("74xx:74LS348",), [wptn([("U1", "1")], "N1")], note="groups: pad number 1 (number first)")
    add(("PI-09", "PI-18"), "pin_name_number/6a_wire_4", f"{PP}/pin_name_number.py 6a wire U1:'4' N4", "probe",
        b_pnn, ("74xx:74LS348",), [wptn([("U1", "4")], "N4")], note="groups: pad number 4 (number first)")
    add(("PI-09", "PI-18"), "pin_name_number/6a_connect_1", f"{PP}/pin_name_number.py 6a connect U1:'1' R9:1",
        "probe", b_pnn, ("74xx:74LS348",), [cp(("U1", "1"), ("R9", "1"))],
        note="groups: pad number 1 (number first)")
    add(("PI-19",), "pin_name_number/6b_wire_GND", f"{PP}/pin_name_number.py 6b wire U1:'GND' NG", "probe",
        b_pnn, ("Amplifier_Current:INA240A1D",), [wptn([("U1", "GND")], "NG", groups=[{("U1", "2")}, {("U1", "4")}])],
        note="groups: every pad named GND (2, 4 stacked)")
    add(("PI-14",), "pin_name_number/6c_wire_GND", f"{PP}/pin_name_number.py 6c wire U1:'GND' NG", "probe",
        b_pnn, ("Amplifier_Audio:LM380N-8",), [wptn([("U1", "GND")], "NG", groups=[{("U1", "4")}, {("U1", "5")}])],
        note="groups: every pad named GND (4, 5)")
    add(("PI-14",), "pin_name_number/6c_connect_GND", f"{PP}/pin_name_number.py 6c connect U1:'GND' R9:1",
        "probe", b_pnn, ("Amplifier_Audio:LM380N-8",),
        [cp(("U1", "GND"), ("R9", "1"), groups=[{("U1", "4"), ("U1", "5"), ("R9", "1")}])],
        note="groups: every pad named GND (4, 5) with R9:1")
    # v06_pinname.py (mapping per its docstring and the caseidx text)
    v06 = [("74xx:74LS348", "1", "N1", True, ("PI-09", "PI-18"), None),
           ("74xx:74LS348", "4", "N4", False, ("PI-09", "PI-18"), None),
           ("Interface_USB:STULPI01A", "D1", "ND1", False, ("PI-09", "PI-18"), None),
           ("Amplifier_Audio:LM380N-8", "GND", "NG", True, ("PI-14",), ("4", "5")),
           ("Analog_ADC:AD7171", "GND", "NG", False, ("PI-14",), ("7", "10")),
           ("Amplifier_Current:INA240A1D", "GND", "NG", False, ("PI-19",), ("2", "4")),
           ("Amplifier_Audio:LM4950TS", "GND", "NG", False, ("PI-19",), ("4", "5"))]
    for lib, arg, N, conn, cases, pads in v06:
        stem = lib.split(":")[1]
        gw = [{("U1", n)} for n in pads] if pads else None
        note = f"groups: every pad named {arg} ({', '.join(pads)})" if pads else \
            f"groups: pad number {arg} (number first)"
        add(cases, f"v06_pinname/{stem}_{arg}_wire", f"{VP}/v06_pinname.py {lib} U1:{arg!r} -> {N}", "verify",
            b_v06, (lib,), [wptn([("U1", arg)], N, groups=gw)], note=note)
        if conn:
            gc = [{("U1", n) for n in pads} | {("R9", "1")}] if pads else None
            add(cases, f"v06_pinname/{stem}_{arg}_connect", f"{VP}/v06_pinname.py {lib} connect U1:{arg!r} R9:1",
                "verify", b_v06, (lib,), [cp(("U1", arg), ("R9", "1"), groups=gc)], note=note)
    # v06d_stacked_single.py (scan pick from its .out: STM32F100C_4-6_Tx 'VSS', pads 23/35/47)
    gv = [{("U1", "23")}, {("U1", "35")}, {("U1", "47")}]
    add(("PI-19",), "v06d_stacked_single/VSS", f"{VP}/v06d_stacked_single.py wire U1:'VSS' NPWR", "verify",
        b_v06d, steps=[wptn([("U1", "VSS")], "NPWR", groups=gv)],
        note="part from the script's own scan output; groups: pads 23, 35, 47")
    add(("PI-19",), "v06d_stacked_single/23", f"{VP}/v06d_stacked_single.py wire U1:'23' NPWR (remedy call,"
        " M only in the original)", "verify", b_v06d, steps=[wptn([("U1", "23")], "NPWR")])

    # nc_hidden.py
    add(("PI-10",), "nc_hidden/7a", f"{PP}/nc_hidden.py 7a wire J1:4 N4", "probe", b_nch_7a,
        steps=[wptn([("J1", "4")], "N4")])
    add(("PI-10",), "nc_hidden/7c", f"{PP}/nc_hidden.py 7c connect J1:4 R9:1", "probe", b_nch_7a,
        steps=[cp(("J1", "4"), ("R9", "1"))])
    add(("PI-10",), "nc_hidden/7b", f"{PP}/nc_hidden.py 7b wire J1:4 N4 (legacy wire to R5:1, N4 on R6:1)",
        "probe", b_nch_7b, steps=[wptn([("J1", "4")], "N4")])
    add(("PI-19", "PI-20"), "nc_hidden/8a", f"{PP}/nc_hidden.py 8a wire U1:4 NG", "probe", b_nch_8a,
        steps=[wptn([("U1", "4")], "NG")])
    add(("PI-20",), "nc_hidden/8b", f"{PP}/nc_hidden.py 8b wire R1:1 NA", "probe", b_nch_8bc, ("HPAS",),
        [wptn([("R1", "1")], "NA")])
    add(("PI-20",), "nc_hidden/8c", f"{PP}/nc_hidden.py 8c wire R1:1 NA", "probe", b_nch_8bc, ("HPWR",),
        [wptn([("R1", "1")], "NA")])
    add(("PI-20",), "nc_hidden/8d_NA", f"{PP}/nc_hidden.py 8d wire U1:2 NA", "probe", b_nch_8bc, ("HPWR",),
        [wptn([("U1", "2")], "NA")])
    add(("PI-20",), "nc_hidden/8d_VCC", f"{PP}/nc_hidden.py 8d wire U1:2 VCC", "probe", b_nch_8bc, ("HPWR",),
        [wptn([("U1", "2")], "VCC")])
    # pad_alt_order.py
    add(("PI-11",), "pad_alt_order/9_wire", f"{PP}/pad_alt_order.py 9 wire RN1:1 NC1", "probe", b_pao_9,
        steps=[wptn([("RN1", "1")], "NC1")])
    add(("PI-11",), "pad_alt_order/9_connect", f"{PP}/pad_alt_order.py 9 connect RN1:1 R9:1", "probe",
        b_pao_9, steps=[cp(("RN1", "1"), ("R9", "1"))])
    add(("PI-10",), "pad_alt_order/10_NCX", f"{PP}/pad_alt_order.py 10_NCX wire U1:1 NA", "probe", b_pao_10,
        ("NCX",), [wptn([("U1", "1")], "NA")])
    add(("PI-21",), "pad_alt_order/10_SDA", f"{PP}/pad_alt_order.py 10_SDA wire U1:'SDA' NA", "probe",
        b_pao_10, ("SDA",), [wptn([("U1", "SDA")], "NA", groups=[{("U1", "1")}])],
        note="groups: pad 1 (its selected alternate is SDA)")
    add(("PI-13",), "pad_alt_order/11", f"{PP}/pad_alt_order.py 11 wire R1:1 N1 ((mirror x) before (at))",
        "probe", b_pao_11, steps=[wptn([("R1", "1")], "N1")])
    # v07_nc.py (second part from the script's own scan output: Interface_UART:ADM1491EBR pin 8)
    for lib, num in (("Connector:AVR-TPI-6", "4"), ("Interface_UART:ADM1491EBR", "8")):
        ref = "J1" if lib.startswith("Connector") else "U7"
        stem = lib.split(":")[1]
        add(("PI-10",), f"v07_nc/N1_{stem}", f"{VP}/v07_nc.py N1 {lib} wire {ref}:{num} N4", "verify",
            b_v07_n13, (lib, ref), [wptn([(ref, num)], "N4")])
        add(("PI-10",), f"v07_nc/N3_{stem}", f"{VP}/v07_nc.py N3 {lib} connect {ref}:{num} R9:1", "verify",
            b_v07_n13, (lib, ref), [cp((ref, num), ("R9", "1"))])
        add(("PI-10",), f"v07_nc/N2_{stem}", f"{VP}/v07_nc.py N2 {lib} wire {ref}:{num} N4 (legacy wire to"
            " R5:1, N4 on R6:1)", "verify", b_v07_n2, (lib, ref, num), [wptn([(ref, num)], "N4")])
    add(("PI-10",), "v07_nc/N4_NCX", f"{VP}/v07_nc.py N4 alternate NCX wire U1:'1' NA", "verify", b_v07_n4,
        ("NCX",), [wptn([("U1", "1")], "NA")])
    add(("PI-21",), "v07_nc/N4_SDA", f"{VP}/v07_nc.py N4 alternate SDA wire U1:'SDA' NA", "verify",
        b_v07_n4, ("SDA",), [wptn([("U1", "SDA")], "NA", groups=[{("U1", "1")}])],
        note="groups: pad 1 (its selected alternate is SDA)")
    # v08_padcopies.py (part from the script's own scan output: Device:D_Dual_CommonAnode_AKK_Split pad 1)
    add(("PI-11",), "v08_padcopies/wire", f"{VP}/v08_padcopies.py PC wire Q5:1 NC1", "verify", b_v08,
        steps=[wptn([("Q5", "1")], "NC1")])
    add(("PI-11",), "v08_padcopies/connect", f"{VP}/v08_padcopies.py PC connect Q5:1 R9:1", "verify", b_v08,
        steps=[cp(("Q5", "1"), ("R9", "1"))])
    # v09_mirror_order.py
    add(("PI-13",), "v09_mirror_order/MO", f"{VP}/v09_mirror_order.py MO wire D1:1 N1 ((mirror y) before"
        " (at ... 180))", "verify", b_v09, steps=[wptn([("D1", "1")], "N1")])

    ids = [j.variant for j in jobs]
    assert len(ids) == len(set(ids)), "duplicate variant ids"
    return jobs


JOBS = build_jobs()

if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--out")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--resume", action="store_true")
    a = ap.parse_args()
    jobs = JOBS
    if a.resume:
        out = Path(a.out or rlib.OUT / f"records_{CAT}.jsonl")
        have: dict = {}
        if out.exists():
            for line in open(out, encoding="utf-8"):
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("status") != "HARNESS_ERROR":
                    have[r["variant"]] = have.get(r["variant"], 0) + 1
        done = {j.variant for j in JOBS if have.get(j.variant, 0) == len(rlib.DESIGNS) * len(j.cases) * len(j.steps)}
        bad = {v for v in have if v not in done}
        if bad:
            sys.exit(f"incomplete or unexpected records in {out}: {sorted(bad)}")
        jobs = [j for j in JOBS if j.variant not in done]
        print(f"resume: {len(done)} jobs complete in {out}, {len(jobs)} to run")
    if a.list:
        from collections import Counter

        c = Counter(case for j in JOBS for case in j.cases)
        print(len(JOBS), "jobs;", dict(sorted(c.items())))
        print("by source:", Counter(j.source for j in JOBS))
        for j in JOBS[:3]:
            print(" ", j.variant, j.cases)
        sys.exit(0)
    rlib.main(jobs, CAT, str(Path(__file__).resolve()), out=a.out, workers=a.workers, only=a.only)
