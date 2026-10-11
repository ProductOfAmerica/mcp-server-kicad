"""Helpers for the composite follow-up 2 re-run (designs M2, H, Hb, E; follow-up 1 ran M2 and K). Import AFTER rlib (rlib redirects TMP/TEMP).

Fixture builders are copied verbatim (geometry, references, call arguments) from
  probe-composite/cplib.py      cfg_loose / cfg_root / cfg_sub        (cap at (127, 101.6))
  verify-probe-composite/vlib.py fx_loose / fx_rootpro / fx_sub       (cap at (152.4, 127))
  probe-composite/c02, c05, c06 and verify-probe-composite/v01, v05 build() functions.
Nothing here imports or runs an original script.

External actors (copied from v02_crash_hold.run / v03_restore_semantics.drive): the call runs in
a child process (cmp_child.py, no hooks unless an exit-after-write is asked for, as c04_child
did); this process busy-polls the watched files' (size, mtime_ns) and on the k-th change kills
the child (TerminateProcess), opens plain read handles (no FILE_SHARE_DELETE, so os.replace onto
the file fails), or performs a second writer's edit (own tmp + os.replace).

State records (kind "state" / "state_end"): per file of the fixture directory, whether it is
unchanged, equal to what an uninterrupted call of the same design writes (uuid-normalised),
zero-length, unparseable, or some other content; .tmp leftovers; and the on-disk state judged
with rlib.Spec(...).snapshot/.assess against the pre-call fixture (unchanged states are not
exported: an unchanged project cannot differ from its own before-reading).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid as _uuid
from pathlib import Path

import hpaths  # noqa: F401  (an entry script has put it on sys.path)
import readings
import rlib as R
from rlib import H, SCH

HERE = Path(__file__).resolve().parent
CHILD = HERE / "cmp_child.py"
PY = hpaths.PYTHON
DEMO = Path(hpaths.KICAD_DEMOS) / "tiny_tapeout"
_UUID = re.compile(rb"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def place(p, lib, ref, x, y, rot=0, value=None, pro=""):
    SCH.place_component(lib_id=lib, reference=ref, value=value or ref, x=x, y=y, rotation=rot,
                        schematic_path=p, project_path=pro)


# ----------------------------------------------------------------------------- cplib fixtures


def cfg_loose(tag):
    p = H.fresh(tag)
    SCH.place_component(lib_id="Device:R", reference="R9", value="1k", x=50.8, y=50.8,
                        schematic_path=p)
    return {"cfg": "loose", "sch": p, "root": p, "files": [p], "netlist": p,
            "project_path": "", "dir": str(Path(p).parent)}


def cfg_root(tag):
    d = H.BASE / tag
    H.project.create_project(str(d), tag)
    p, pro = str(d / f"{tag}.kicad_sch"), str(d / f"{tag}.kicad_pro")
    SCH.place_component(lib_id="Device:R", reference="R9", value="1k", x=50.8, y=50.8,
                        schematic_path=p, project_path=pro)
    return {"cfg": "root+pro", "sch": p, "root": p, "files": [p], "netlist": p,
            "project_path": pro, "dir": str(d), "pro": pro}


def cfg_sub(tag, pass_project=True):
    d = H.BASE / tag
    H.project.create_project(str(d), tag)
    root, pro = str(d / f"{tag}.kicad_sch"), str(d / f"{tag}.kicad_pro")
    kid = str(d / "kid.kicad_sch")
    H.project.create_schematic(kid)
    H.project.add_hierarchical_sheet(parent_schematic_path=root, sheet_name="kid", sheet_file=kid,
                                     pins=[], x=152.4, y=25.4, project_path=pro)
    SCH.place_component(lib_id="Device:R", reference="R9", value="1k", x=50.8, y=50.8,
                        schematic_path=kid, project_path=pro)
    return {"cfg": "sub+pro" if pass_project else "sub, project_path omitted", "sch": kid,
            "files": [kid, root], "netlist": root, "project_path": pro if pass_project else "",
            "dir": str(d), "pro": pro, "root": root}


CONFIGS = {"loose": cfg_loose, "root": cfg_root, "sub": cfg_sub,
           "subnp": lambda tag: cfg_sub(tag, pass_project=False)}


def cap_kw_c(fx, **extra):
    """cplib-style cap call (c01..c04): Device:C C1 at (127, 101.6), VCC/GND."""
    kw = dict(lib_id="Device:C", reference="C1", value="100n", x=127, y=101.6,
              power_net="VCC", ground_net="GND", project_path=fx["project_path"])
    kw.update(extra)
    return kw


def add_t1(fx):
    """c02/c03/c04 trigger T1: R9:1 named global FOO, and global FOO where C1:2 lands."""
    s = fx["sch"]
    H.wire(s, 50.8, 46.99, 50.8, 44.45)
    H.label(s, "FOO", 50.8, 44.45, kind="global_label")
    H.label(s, "FOO", 127, 105.41, kind="global_label")


def add_trigger(fx, trig):
    """c02_rollback_exact.add_trigger, verbatim geometry."""
    s = fx["sch"]
    if trig == "T1":
        H.wire(s, 50.8, 46.99, 50.8, 44.45)
        H.label(s, "FOO", 50.8, 44.45, kind="global_label")
        H.label(s, "FOO", 127, 105.41, kind="global_label")  # where C1:2 will land
        return dict(lib_id="Device:C")
    if trig == "T2":
        return dict(lib_id="Connector:TestPoint")
    if trig == "T3":
        H.wire(s, 50.8, 46.99, 50.8, 44.45)
        H.label(s, "FOO", 50.8, 44.45, kind="global_label")
        H.wire(s, 127, 97.79, 139.7, 97.79)  # its left end is where C1:1 will land
        H.label(s, "FOO", 139.7, 97.79, kind="global_label")
        return dict(lib_id="Device:C")
    raise ValueError(trig)


# ----------------------------------------------------------------------------- vlib fixtures


def fx_loose(tag):
    p = H.fresh(tag)
    place(p, "Device:R", "R90", 38.1, 38.1)  # a bystander part, so the sheet has nets
    return {"cfg": "loose", "sch": p, "root": p, "files": [p], "net": p, "pro": "",
            "dir": str(Path(p).parent)}


def fx_rootpro(tag):
    d = H.BASE / tag
    H.project.create_project(str(d), tag)
    p, pro = str(d / f"{tag}.kicad_sch"), str(d / f"{tag}.kicad_pro")
    place(p, "Device:R", "R90", 38.1, 38.1, pro=pro)
    return {"cfg": "root+pro", "sch": p, "root": p, "files": [p], "net": p, "pro": pro,
            "dir": str(d)}


def fx_sub(tag, pass_pro=True):
    d = H.BASE / tag
    H.project.create_project(str(d), tag)
    root, pro = str(d / f"{tag}.kicad_sch"), str(d / f"{tag}.kicad_pro")
    kid = str(d / "child.kicad_sch")
    H.project.create_schematic(kid)
    H.project.add_hierarchical_sheet(parent_schematic_path=root, sheet_name="child",
                                     sheet_file=kid, pins=[], x=177.8, y=38.1, project_path=pro)
    place(kid, "Device:R", "R90", 38.1, 38.1, pro=pro)
    return {"cfg": "sub+pro" if pass_pro else "sub(no pro arg)", "sch": kid,
            "files": [kid, root], "net": root, "pro": pro if pass_pro else "", "dir": str(d),
            "root": root}


FX = {"loose": fx_loose, "root": fx_rootpro, "sub": fx_sub}


def cap_kw_v(fx, x=152.4, y=127, pn="VCC", gn="GND", ref="C1", lib="Device:C", **extra):
    """vlib-style cap call (v01..v07)."""
    kw = dict(lib_id=lib, reference=ref, value="100n", x=x, y=y, power_net=pn, ground_net=gn,
              project_path=fx["pro"])
    kw.update(extra)
    return kw


def foo_at_pin2(fx, x=152.4, y=127):
    """v02/v03: global FOO on R90:1 and where C1:2 will land, so pin 2 must refuse."""
    s = fx["sch"]
    H.label(s, "FOO", 38.1, 34.29, kind="global_label")            # on R90:1 (38.1, 34.29)
    H.label(s, "FOO", x, round(y + 3.81, 2), kind="global_label")


def demo_fx(tag):
    """A copy of KiCad's tiny_tapeout demo (1.17 MB root sheet) under H.BASE/tag/tt."""
    dd = H.BASE / tag / "tt"
    shutil.copytree(DEMO, dd)
    s = str(dd / "tinytapeout-demo.kicad_sch")
    return {"cfg": "tinytapeout root+pro", "sch": s, "root": s,
            "pro": str(dd / "tinytapeout-demo.kicad_pro"), "dir": str(dd), "files": [s],
            "net": s}


# ----------------------------------------------------------------------------- copies, bytes


def norm(data: bytes) -> bytes:
    return _UUID.sub(b"U", data)


def files_of(d) -> dict:
    d = Path(d)
    return {str(f.relative_to(d)): f.read_bytes() for f in sorted(d.rglob("*")) if f.is_file()}


def sch_bytes(d) -> dict:
    d = Path(d)
    return {str(f.relative_to(d)): f.read_bytes() for f in sorted(d.rglob("*.kicad_sch"))}


def copy_dir(src, name) -> Path:
    src = Path(src)
    dst = src.parent / name
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    return dst


def rel(path, base_dir, new_dir) -> Path:
    return Path(new_dir) / Path(path).resolve().relative_to(Path(base_dir).resolve())


def reference_output(design, base_dir, sch_path, kw) -> tuple[str, str, dict]:
    """Run the same call uninterrupted, in-process, on a copy: (status, msg, {file: norm bytes})."""
    d = copy_dir(base_dir, Path(base_dir).name + f"__ref_{design}")
    st, msg = R.call(R.IMPLS[design].auto_place_decoupling_cap,
                     schematic_path=str(rel(sch_path, base_dir, d)), **remap(kw, base_dir, d))
    out = {k: norm(v) for k, v in sch_bytes(d).items()}
    shutil.rmtree(d, ignore_errors=True)
    return st, msg, out


def classify_files(pre: dict, now_dir, ref: dict) -> tuple[dict, list]:
    """Per .kicad_sch: unchanged / equals_uninterrupted / zero_length / unparseable / other.
    Also the .tmp leftovers anywhere in the directory."""
    now = sch_bytes(now_dir)
    out = {}
    for k in sorted(set(pre) | set(now)):
        b = now.get(k)
        if b is None:
            out[k] = "MISSING"
        elif b == pre.get(k):
            out[k] = "unchanged"
        elif ref.get(k) is not None and norm(b) == ref[k]:
            out[k] = "equals_uninterrupted"
        elif len(b) == 0:
            out[k] = "zero_length"
        else:
            try:
                R._cst.parse(b)
                out[k] = "other"
            except Exception:  # noqa: BLE001
                out[k] = "unparseable"
    tmps = sorted(str(f.relative_to(now_dir)) for f in Path(now_dir).rglob("*")
                  if f.is_file() and f.name.endswith(".tmp"))
    return out, tmps


# ----------------------------------------------------------------------------- the child


def _child_env():
    t = R.TMPDIR / "child"
    t.mkdir(parents=True, exist_ok=True)
    return hpaths.child_env(TMP=str(t), TEMP=str(t))


def start_child(design, sch, kw, mode="plain"):
    kwf = R.TMPDIR / f"kw_{_uuid.uuid4().hex[:8]}.json"
    kwf.write_text(json.dumps(kw), encoding="utf-8")
    proc = subprocess.Popen([PY, "-B", str(CHILD), design, str(sch), str(kwf), mode],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8",
                            errors="replace", env=_child_env(), cwd=str(HERE))
    pre = []
    while True:
        line = proc.stdout.readline()
        if not line:
            raise RuntimeError(f"child died before ready: {proc.stderr.read()[-800:]}")
        if line.strip() == "ready":
            break
        pre.append(line.rstrip())
    return proc


def stat_key(path):
    st = os.stat(path)
    return (st.st_size, st.st_mtime_ns)


def _open_retry(f, limit=0.2):
    t0 = time.perf_counter()
    while True:
        try:
            return open(f, "rb")  # noqa: SIM115
        except PermissionError:
            if time.perf_counter() - t0 > limit:
                raise


MARK = b"EXTERNAL EDIT BY ANOTHER WRITER"


def external_edit(path):
    """v03.external_edit, verbatim: append a text node through its own tmp + os.replace."""
    data = Path(path).read_bytes()
    i = data.rstrip().rfind(b")")
    node = (b'\n\t(text "' + MARK + b'"\n\t\t(exclude_from_sim no)\n\t\t(at 50.8 160.02 0)\n'
            b'\t\t(effects\n\t\t\t(font\n\t\t\t\t(size 1.27 1.27)\n\t\t\t)\n\t\t)\n\t\t(uuid "'
            + str(_uuid.uuid4()).encode() + b'")\n\t)\n')
    tmp = str(path) + ".ext.tmp"
    Path(tmp).write_bytes(data[:i] + node + data[i:])
    for _ in range(50):
        try:
            os.replace(tmp, path)
            return True
        except PermissionError:
            time.sleep(0.005)
    return False


def drive(design, sch, kw, *, action=None, watch=None, k=1, targets=(), secs=3.0,
          delay_s=None, hold_start=False, mode="plain", on_act=None, on_exit=None):
    """Run one call in a child and act on it from outside.

    action: None | "kill" | "hold" | "edit". The action fires on the k-th observed change of
    `watch`, or (delay_s) that many seconds after the child is ready if it has not fired by then.
    hold_start: hold `targets` from the moment the child is ready (v02 HS, c03 A1/A3/A5/A6).
    on_act(): called right after the action is performed (state capture at the interruption).
    on_exit(res): called right after the child has exited, BEFORE any remaining hold is released.
    Returns a dict: child status/msg, killed, fired (how), events, observed changes.
    """
    proc = start_child(design, sch, kw, mode=mode)
    t0 = time.perf_counter()
    held, hold_until = [], 0.0
    events = []
    if hold_start:
        held = [open(f, "rb") for f in targets]  # noqa: SIM115
        hold_until = time.perf_counter() + secs
        events.append((0, "HOLD from ready on " + ",".join(Path(f).name for f in targets)))
    watched = {str(Path(w)) for w in ([watch] if watch else [])} | {str(Path(sch))}
    keys = {}
    for w in watched:
        try:
            keys[w] = stat_key(w)
        except OSError:
            keys[w] = None
    counts = {w: 0 for w in watched}
    stamps = []
    fired, killed, fired_how = False, False, None
    while proc.poll() is None:
        now = time.perf_counter()
        for w in watched:
            try:
                k2 = stat_key(w)
            except OSError:
                continue
            if k2 != keys[w]:
                keys[w] = k2
                counts[w] += 1
                ms = round((now - t0) * 1000)
                events.append((ms, Path(w).name))
                if w == str(Path(sch)):
                    stamps.append(ms)
        if action and not fired:
            trig = watch and counts.get(str(Path(watch)), 0) >= k
            late = delay_s is not None and (now - t0) >= delay_s
            if trig or late:
                fired, fired_how = True, ("watch" if trig else f"delay {delay_s}s")
                if action == "kill":
                    proc.kill()
                    killed = True
                    events.append((round((time.perf_counter() - t0) * 1000), "KILL"))
                    if on_act:
                        proc.wait()
                        on_act()
                    break
                if action == "hold":
                    held = [_open_retry(f) for f in targets]
                    hold_until = time.perf_counter() + secs
                    events.append((round((time.perf_counter() - t0) * 1000),
                                   "HOLD on " + ",".join(Path(f).name for f in targets)))
                elif action == "edit":
                    ok = [external_edit(f) for f in targets]
                    for f in targets:
                        if str(Path(f)) in keys:
                            keys[str(Path(f))] = stat_key(f)
                    events.append((round((time.perf_counter() - t0) * 1000),
                                   f"SECOND WRITER edited {','.join(Path(f).name for f in targets)}"
                                   f" ok={ok}"))
                if on_act:
                    on_act()
        if held and now > hold_until:
            for h in held:
                h.close()
            held = []
            events.append((round((now - t0) * 1000), "RELEASE"))
        # no sleep (v02): Windows sleeps >= ~15.6 ms and would miss writes close together
    out, err = proc.communicate()
    exit_ms = round((time.perf_counter() - t0) * 1000)
    res = {"killed": killed, "fired": fired, "fired_how": fired_how, "events": events,
           "observed_changes": counts.get(str(Path(sch)), 0), "change_ms": stamps,
           "exit_ms": exit_ms, "held_at_exit": bool(held), "rc": proc.returncode,
           "child_log": [ln for ln in out.splitlines() if ln.startswith("write ")]}
    lines = [ln for ln in out.splitlines() if ln.startswith("{")]
    if killed:
        res["status"], res["msg"] = "KILLED", ""
    elif lines:
        c = json.loads(lines[-1])
        res["status"], res["msg"] = c["status"], c["msg"]
    else:
        res["status"] = "EXITED" if proc.returncode == 9 else "NO OUTPUT"
        res["msg"] = (err or "")[-600:]
    res["hold_release_wait"] = None
    if on_exit:
        on_exit(res)
    if held:
        while time.perf_counter() < hold_until:
            time.sleep(0.01)
        for h in held:
            h.close()
        res["hold_release_wait"] = True
        events.append((round((time.perf_counter() - t0) * 1000), "RELEASE (after exit)"))
    shutil.rmtree(R.TMPDIR / "child", ignore_errors=True)
    return res


# ----------------------------------------------------------------------------- path remapping
# rlib.run_case / run_sequence / fallbacks pass kw unchanged to each design's copy. A kw value
# that is a path INTO the base fixture (project_path) then makes the design read and WRITE the
# base fixture (M2/K's _root_index writes the root named by project_path), so the per-design
# copy misses the root write and the next design's copy inherits it (measured in dbg_root.py:
# the base root accumulated one symbol_instances entry per call). These copies remap such paths.


def remap(kw: dict, base_dir, new_dir) -> dict:
    base_dir, new_dir = Path(base_dir).resolve(), Path(new_dir)
    out = {}
    for k, v in kw.items():
        if isinstance(v, str) and v and os.path.isabs(v):
            try:
                rp = Path(v).resolve().relative_to(base_dir)
                v = str(new_dir / rp)
            except ValueError:
                pass
        out[k] = v
    return out


def _tree_sig(d):
    return R.tree_hashes(d)


def fallbacks_cmp(case, variant, tool, kw, spec, pre_dir, root, edited, refusing, also=(),
                  note="", step=None):
    """rlib v3's fallbacks with kw paths remapped into each fallback copy."""
    return R.fallbacks(case, variant, tool, kw, spec, pre_dir, root, edited, refusing, also=also,
                       note=note, step=step, kwmap=remap)


def run_case(case, variant, tool, edited, kw, *, root=None, script=None, also=(), note="",
             designs=R.DESIGNS, fallback=True):
    """rlib v3's run_case with kw paths remapped into each design's copy and base_mutated checked
    (follow-up 1's cmplib.run_case, moved into rlib v3 as kwmap / check_base)."""
    return R.run_case(case, variant, tool, edited, kw, root=root, script=script, also=also,
                      note=note, designs=designs, fallback=fallback, kwmap=remap, check_base=True)


def run_sequence(case, variant, steps, edited, *, root=None, script=None, also=(), note="",
                 designs=R.DESIGNS, fallback=True):
    """rlib v3's run_sequence with kw paths remapped into each design's evolving copy."""
    return R.run_sequence(case, variant, steps, edited, root=root, script=script, also=also,
                          note=note, designs=designs, fallback=fallback, kwmap=remap,
                          check_base=True)


# ----------------------------------------------------------------------------- cap geometry
# (CMP-04: where the new stubs and labels land relative to the placed cap; copied from
#  vlib.cap_geometry / classify / new_items and v04.collinear_with_pin, integer KiCad transform)

_ROT = {0: (1, 0, 0, 1), 90: (0, 1, -1, 0), 180: (-1, 0, 0, -1), 270: (0, -1, 1, 0)}
_TOW = {0: (1, 0), 90: (0, -1), 180: (-1, 0), 270: (0, 1)}


def _iu(v) -> int:
    s = float(v) * 10000.0
    return int(s + 0.5) if s >= 0 else -int(-s + 0.5)


def _tf(t, x, y):
    return t[0] * x + t[1] * y, t[2] * x + t[3] * y


def cap_geometry(data: bytes, ref):
    root = R._cst.parse(data).lists[0]
    sh = R._shared
    sym = next(s for s in root.find_all("symbol") if sh._sym_property_cst(s, "Reference") == ref)
    key = sym.find("lib_name").atoms[1].text if sym.find("lib_name") is not None else \
        sym.find("lib_id").atoms[1].text
    ls = next(x for x in root.find("lib_symbols").find_all("symbol") if x.atoms[1].text == key)
    at = sym.find("at")
    c = (_iu(at.atoms[1].text), _iu(at.atoms[2].text))
    rot = int(float(at.atoms[3].text)) if len(at.atoms) > 3 else 0
    t = _ROT[rot]

    def tr(lx, ly):
        dx, dy = _tf(t, _iu(lx), -_iu(ly))
        return c[0] + dx, c[1] + dy

    gfx, pins = [], {}
    for sub in ls.find_all("symbol"):
        for poly in sub.find_all("polyline"):
            for xy in poly.find("pts").find_all("xy"):
                gfx.append(tr(xy.atoms[1].text, xy.atoms[2].text))
        for rect in sub.find_all("rectangle"):
            for kk in ("start", "end"):
                n = rect.find(kk)
                gfx.append(tr(n.atoms[1].text, n.atoms[2].text))
        for pn in sub.find_all("pin"):
            pat = pn.find("at")
            conn = tr(pat.atoms[1].text, pat.atoms[2].text)
            ang = int(float(pat.atoms[3].text)) if len(pat.atoms) > 3 else 0
            ln = _iu(pn.find("length").atoms[1].text)
            tw = _tf(t, *_TOW[ang])
            num = pn.find("number").atoms[1].text
            pins[num] = (conn, (conn[0] + tw[0] * ln, conn[1] + tw[1] * ln))
    xs = [q[0] for q in gfx]
    ys = [q[1] for q in gfx]
    return {"origin": c, "rot": rot, "gfx_bbox": (min(xs), min(ys), max(xs), max(ys)),
            "pins": pins}


def classify_pt(q, g) -> str:
    for num, (conn, _end) in g["pins"].items():
        if q == conn:
            return f"pin{num}-end"
    x0, y0, x1, y1 = g["gfx_bbox"]
    if x0 <= q[0] <= x1 and y0 <= q[1] <= y1:
        return "INSIDE-BODY-BBOX"
    for num, (conn, end) in g["pins"].items():
        (ax, ay), (bx, by) = conn, end
        if min(ax, bx) <= q[0] <= max(ax, bx) and min(ay, by) <= q[1] <= max(ay, by) and \
                (bx - ax) * (q[1] - ay) - (by - ay) * (q[0] - ax) == 0:
            return f"ON-PIN{num}-LINE"
    return "outside"


def collinear_with_pin(a, b, g):
    out = []
    for num, (conn, end) in g["pins"].items():
        def orient(o, p, q):
            return (p[0] - o[0]) * (q[1] - o[1]) - (p[1] - o[1]) * (q[0] - o[0])
        if orient(a, b, conn) == 0 and orient(a, b, end) == 0:
            if a[0] == b[0]:
                lo, hi, plo, phi = min(a[1], b[1]), max(a[1], b[1]), min(conn[1], end[1]), \
                    max(conn[1], end[1])
            else:
                lo, hi, plo, phi = min(a[0], b[0]), max(a[0], b[0]), min(conn[0], end[0]), \
                    max(conn[0], end[0])
            if min(hi, phi) - max(lo, plo) > 0:
                out.append(num)
    return out


def new_items(before: bytes, after: bytes):
    sh = R._shared

    def grab(data):
        root = R._cst.parse(data).lists[0]
        out = {}
        for tok in ("wire", "label", "junction", "symbol", "global_label", "text"):
            for n in root.find_all(tok):
                out[(tok, sh._node_uuid(n))] = n
        return out

    b, a = grab(before), grab(after)
    res = []
    for (tok, _u), n in a.items():
        if (tok, _u) in b:
            continue
        if tok == "wire":
            xs = n.find("pts").find_all("xy")
            res.append(("wire", (_iu(xs[0].atoms[1].text), _iu(xs[0].atoms[2].text)),
                        (_iu(xs[1].atoms[1].text), _iu(xs[1].atoms[2].text))))
        elif tok in ("label", "global_label"):
            at = n.find("at")
            res.append((tok, n.atoms[1].text, (_iu(at.atoms[1].text), _iu(at.atoms[2].text)),
                        at.atoms[3].text if len(at.atoms) > 3 else "0"))
        elif tok == "junction":
            at = n.find("at")
            res.append(("junction", (_iu(at.atoms[1].text), _iu(at.atoms[2].text))))
        elif tok == "symbol":
            res.append(("symbol", sh._sym_property_cst(n, "Reference")))
        else:
            res.append((tok, n.atoms[1].text))
    return res


def mmq(q) -> str:
    return f"({q[0] / 1e4:g}, {q[1] / 1e4:g})"


def geom_check(design, base_dir, sch_path, kw, ref="C1"):
    """Run the call once more on a scratch copy (outputs are deterministic apart from uuids)
    and classify every new wire/label against the placed cap (v04's method)."""
    d = copy_dir(base_dir, Path(base_dir).name + f"__geo_{design}")
    s2 = rel(sch_path, base_dir, d)
    before = Path(s2).read_bytes()
    st, msg = R.call(R.IMPLS[design].auto_place_decoupling_cap, schematic_path=str(s2),
                     **remap(kw, base_dir, d))
    out = {"design": design, "status": st}
    if st == "OK":
        after = Path(s2).read_bytes()
        g = cap_geometry(after, ref)
        desc, bad, touched = [], [], set()
        for it in new_items(before, after):
            if it[0] == "wire":
                a, b = it[1], it[2]
                ca, cb = classify_pt(a, g), classify_pt(b, g)
                ov = collinear_with_pin(a, b, g)
                for c in (ca, cb):
                    if c.endswith("-end"):
                        touched.add(c)
                s = f"wire {mmq(a)}[{ca}] -> {mmq(b)}[{cb}]" + (f" OVERLAPS PIN {ov} LINE"
                                                                if ov else "")
                desc.append(s)
                if ov or "INSIDE" in cb or "LINE" in cb:
                    bad.append(s)
            elif it[0] in ("label", "global_label"):
                c = classify_pt(it[2], g)
                if c.endswith("-end"):
                    touched.add(c)
                s = f"{it[0]} {it[1]} at {mmq(it[2])} rot {it[3]} [{c}]"
                desc.append(s)
                if "INSIDE" in c or "LINE" in c:
                    bad.append(s)
            elif it[0] == "junction":
                desc.append(f"JUNCTION {mmq(it[1])}")
                bad.append(f"JUNCTION {mmq(it[1])}")
        out.update(items=desc, inside=bad,
                   transform_crosscheck=(touched == {"pin1-end", "pin2-end"}))
    shutil.rmtree(d, ignore_errors=True)
    return out


# ----------------------------------------------------------------------------- state records


class Exp:
    """One external-actor experiment fixture: pristine base, lazily computed pre-call snapshot,
    uninterrupted reference outputs per design."""

    def __init__(self, fx, kw):
        self.fx, self.kw = fx, kw
        self.base_dir = Path(fx["dir"]).resolve()
        self.root = Path(fx["root"]).resolve()
        self.sch = Path(fx["sch"]).resolve()
        self.pre = sch_bytes(self.base_dir)
        self.spec = R.Spec("auto_place_decoupling_cap", kw, self.sch)
        self._snap = None
        self.refs = {}

    def snap(self):
        if self._snap is None:
            self._snap = self.spec.snapshot(self.root, self.sch)
            R.wipe_judge_scratch()
        return self._snap

    def ref(self, design):
        if design not in self.refs:
            self.refs[design] = reference_output(design, self.base_dir, self.sch, self.kw)
        return self.refs[design]

    def design_copy(self, design, suffix=""):
        """(dir, edited, root, kw remapped into the copy) for one design's run."""
        d = copy_dir(self.base_dir, self.base_dir.name + f"__{design}{suffix}")
        return (d, rel(self.sch, self.base_dir, d), rel(self.root, self.base_dir, d),
                remap(self.kw, self.base_dir, d))

    def judge_dir(self, d, status):
        """Classify and judge the state in directory d (a copy) against the pre-call fixture."""
        e2, r2 = rel(self.sch, self.base_dir, d), rel(self.root, self.base_dir, d)
        now = sch_bytes(d)
        changed = sorted(k for k in set(now) | set(self.pre) if now.get(k) != self.pre.get(k))
        rec = {"files_changed": changed, "bytes_unchanged": not changed}
        if not changed:
            rec.update(cli_wrong=False, gui_wrong=False, cli_detail={}, gui_detail={},
                       delivered_cli=False, delivered_gui=False, false_noop=False,
                       pre_satisfied=False, redundant_write=False, extra=None,
                       judge_note="unchanged: not exported")
            for rd in ("cli", "gui"):
                if rd not in readings.READINGS:  # not taken: no verdict, not a vacuous "clean"
                    rec[f"{rd}_wrong"] = rec[f"delivered_{rd}"] = None
            return rec
        try:
            res = self.spec.assess(self.snap(), r2, e2, "OK", True)
        except Exception as e:  # noqa: BLE001
            res = {"cli": {"wrong": None, "error": repr(e)}, "gui": {"wrong": None, "error": repr(e)}}
        R._fill(rec, res)
        R.wipe_judge_scratch()
        return rec


def mark_status(d) -> dict:
    """Does each .kicad_sch in d carry the second writer's text?"""
    return {k: MARK in v for k, v in sch_bytes(d).items()}


def run_experiment(case, variant, script, fx, kw, *, also=(), action=None, watch_key=None, k=1,
                   target_keys=(), secs=3.0, hold_start=False, delay_s=None, retry=False,
                   mode="plain", note="", check_mark=False, extra=None, desc="",
                   designs=R.DESIGNS):
    """One external-actor experiment on byte-identical copies of fx, per design.

    watch_key / target_keys: "sch" (the edited sheet) or "root" (the project root sheet).
    Records: kind "state" = the on-disk state at the interruption (after the kill; right after
    the second writer's edit; for holds and no-actor runs, when the child has exited, before
    any hold is released); kind "state_end" (holds and edits) = after the child exited and every
    hold was released. retry: re-issue the same call afterwards (v02/c04), result recorded.
    """
    exp = Exp(fx, kw)
    print(f"== {case} | {variant} | {desc or action or 'no actor'}"
          + (f" | {note}" if note else ""), flush=True)
    for dz in designs:
        d, e2, r2, kw2 = exp.design_copy(dz)
        paths = {"sch": e2, "root": r2}
        watch = paths[watch_key] if watch_key else None
        targets = [paths[t] for t in target_keys]
        caps = {}

        def at_act(d=d, caps=caps):
            caps["act"] = copy_dir(d, d.name + "__act")

        def at_exit(res, d=d, caps=caps):
            caps["exit"] = copy_dir(d, d.name + "__exit")

        res = drive(dz, e2, kw2, action=action, watch=watch, k=k, targets=targets, secs=secs,
                    delay_s=delay_s, hold_start=hold_start, mode=mode,
                    on_act=at_act if action in ("kill", "edit") else None, on_exit=at_exit)
        end_dir = copy_dir(d, d.name + "__end")
        interruption = {
            "desc": desc, "action": action, "hold_start": hold_start, "k": k,
            "watch": Path(watch).name if watch else None,
            "targets": [Path(t).name for t in targets], "secs": secs, "delay_s": delay_s,
            "mode": mode, "fired": res["fired"] or hold_start, "fired_how": res["fired_how"],
            "events": res["events"], "observed_changes": res["observed_changes"],
            "change_ms": res["change_ms"], "exit_ms": res["exit_ms"],
            "held_at_exit": res["held_at_exit"], "child_log": res["child_log"],
            "rc": res["rc"]}
        ex = dict(extra or {})
        ex["note"] = note
        if retry:
            st, msg = R.call(R.IMPLS[dz].auto_place_decoupling_cap, schematic_path=str(e2), **kw2)
            ex["retry"] = f"{st}: {msg[:200]}"
        state_dir = caps["act"] if action in ("kill", "edit") and "act" in caps else caps["exit"]
        ex1 = dict(ex)
        if check_mark:
            ex1["second_writer_edit_survives"] = mark_status(state_dir)
        state_record(exp, dz, state_dir, res["status"], res["msg"], case=case, variant=variant,
                     script=script, also=also, interruption=interruption, extra=ex1)
        if action in ("hold", "edit") or hold_start:
            ex2 = dict(ex)
            if check_mark:
                ex2["second_writer_edit_survives"] = mark_status(end_dir)
            state_record(exp, dz, end_dir, res["status"], res["msg"], case=case,
                         variant=variant, script=script, also=also, kind="state_end",
                         interruption=interruption, extra=ex2)
        print(f"     events {res['events']} retry={ex.get('retry', '')[:100]!r}", flush=True)
        for x in (d, end_dir, *caps.values()):
            shutil.rmtree(x, ignore_errors=True)
    return exp


def state_record(exp, design, d, status, msg, *, case, variant, script, kind="state",
                 also=(), interruption=None, extra=None):
    """Capture-and-judge the on-disk state of directory d; emit the record and print a line."""
    ref_st, _ref_msg, ref = exp.ref(design)
    files, tmps = classify_files(exp.pre, d, ref)
    rec = dict(case=case, variant=variant, tool="auto_place_decoupling_cap", design=design,
               kind=kind, also=list(also), status=status, codes=R.codes_of(msg or ""),
               msg_head=(msg or "")[:200], msg=(msg or "")[:3000], script=script,
               N=[exp.kw["power_net"], exp.kw["ground_net"]], pads=exp.spec.pads,
               files=files, tmp_leftovers=tmps, uninterrupted_status=ref_st,
               interruption=interruption or {})
    rec.update(exp.judge_dir(d, status))
    if extra:
        clash = set(extra) & {"extra", "cli_wrong", "gui_wrong", "cli_detail", "gui_detail",
                              "delivered_cli", "delivered_gui", "files", "status"}
        assert not clash, f"extra fields would overwrite judged fields: {clash}"
        rec.update(extra)
    R.emit(rec)

    def yn(v):
        return "?" if v is None else ("Y" if v else "N")

    print(f"  [{kind}] {design:>2} {status} files={files} tmp={tmps}"
          f" cli={'WRONG' if rec.get('cli_wrong') else 'clean' if rec.get('cli_wrong') is False else 'n/a'}"
          f" gui={'WRONG' if rec.get('gui_wrong') else 'clean' if rec.get('gui_wrong') is False else 'n/a'}"
          f" dlv={yn(rec.get('delivered_cli'))}/{yn(rec.get('delivered_gui'))}"
          f" | {(msg or '')[:140]!r}", flush=True)
    for side in ("cli", "gui"):
        if rec.get(f"{side}_wrong"):
            print(f"      {side} detail: {rec[f'{side}_detail']}", flush=True)
    return rec
