"""In-process interruption reruns for M2, H, Hb and E (follow-up 2; follow-up 1 ran M2 and K): probe-composite/c03_restore_failure.py rows C
(CMP-06) and D (CMP-07). cplib loose fixtures, cap C1 at (127, 101.6), VCC/GND.

  C  KeyboardInterrupt during pin-2 wiring. c03 raised it from P's second wire_pins_to_net
     call. M2 and K wire both pins inside the call through _wire_request; the hook raises
     KeyboardInterrupt on its second invocation (pin 2), the same point in the composition.
  D  An external edit lands between the refusal and the end of the call. c03 hooked P's
     _restore and made the edit (the repo's add_text) there. M2 and K have no restore: the
     refusal here (C1:2 lands on global FOO, trigger T1) is built by the module's _err, so the
     hook makes the same add_text edit when _err is first called, i.e. after the refusal is
     decided and before the call returns. Recorded: whether the edit survives.
State records (kind "state") judge what is on disk when the call has returned or raised.
Follow-up 2: hooks patch R.MODS[design] (Hb's functions live in hb_impl.MODULE). H and Hb wire
each pin through _wire_request as M2 does; E's auto_place_decoupling_cap calls its _wire once per
pin (pin 1, then pin 2), so for E the C hook wraps _wire, the same composition point.
"""

import sys
from pathlib import Path

sys.dont_write_bytecode = True  # first: hpaths.py would be cached beside the code otherwise
sys.path.insert(0, str(next(p for p in Path(__file__).resolve().parents if (p / "hpaths.py").is_file())))
import hpaths  # noqa: E402

hpaths.bootstrap(__file__)
import rlib as R  # noqa: E402
from rlib import SCH  # noqa: E402

import cmplib as C  # noqa: E402

R.begin("composite", __file__, tag="cmp_inproc_hooks")
S = "probe-composite/c03_restore_failure.py"
TEXT = "EDIT MADE BY THE USER DURING THE CALL"


def run(fn, kw):
    try:
        msg = fn(**kw)
        return "OK", str(msg)
    except BaseException as e:  # noqa: BLE001  (KeyboardInterrupt included on purpose)
        cls = type(e).__name__
        return ("REFUSED" if cls == "ToolError" else cls), f"{cls}: {e}"


# ---- C
fx = C.CONFIGS["loose"]("c03_C")
exp = C.Exp(fx, C.cap_kw_c(fx))
print("== CMP-06 | c03_restore_failure:C | KeyboardInterrupt on the 2nd _wire_request", flush=True)
for dz in R.DESIGNS:
    mod = R.MODS[dz]
    hook = "_wire" if dz == "E" else "_wire_request"
    d, e2, r2, kw2 = exp.design_copy(dz)
    real = getattr(mod, hook)
    cnt = {"n": 0}

    def kb(*a, _real=real, _cnt=cnt, **k):
        _cnt["n"] += 1
        if _cnt["n"] == 2:
            raise KeyboardInterrupt("simulated interrupt during pin-2 wiring")
        return _real(*a, **k)

    setattr(mod, hook, kb)
    try:
        st, msg = run(R.IMPLS[dz].auto_place_decoupling_cap, dict(schematic_path=str(e2), **kw2))
    finally:
        setattr(mod, hook, real)
    C.state_record(exp, dz, d, st, msg, case="CMP-06", variant="c03_restore_failure:C",
                   script=S, interruption={"mode": "inproc KeyboardInterrupt",
                                           "at": f"2nd {hook} (pin 2)",
                                           "fired": cnt["n"] >= 2, "invocations": cnt["n"]})

# ---- D
fx = C.CONFIGS["loose"]("c03_D")
C.add_t1(fx)
exp = C.Exp(fx, C.cap_kw_c(fx))
print("== CMP-07 | c03_restore_failure:D | add_text edit made when the refusal is built",
      flush=True)
for dz in R.DESIGNS:
    mod = R.MODS[dz]
    d, e2, r2, kw2 = exp.design_copy(dz)
    real = mod._err
    fired = {"n": 0, "code": None, "edit": None}

    def err(code, text, _real=real, _f=fired, _p=e2):
        _f["n"] += 1
        if _f["n"] == 1:
            _f["code"] = code
            _f["edit"] = R.call(SCH.add_text, text=TEXT, x=50.8, y=120, schematic_path=str(_p))[0]
        return _real(code, text)

    mod._err = err
    try:
        st, msg = run(R.IMPLS[dz].auto_place_decoupling_cap, dict(schematic_path=str(e2), **kw2))
    finally:
        mod._err = real
    survives = TEXT.encode() in Path(e2).read_bytes()
    C.state_record(exp, dz, d, st, msg, case="CMP-07", variant="c03_restore_failure:D",
                   script=S, interruption={"mode": "inproc edit at the refusal",
                                           "fired": fired["n"] >= 1, "at_code": fired["code"],
                                           "edit_status": fired["edit"]},
                   extra={"second_writer_edit_survives": {Path(e2).name: survives}})
    print(f"     external text still in the file: {survives}", flush=True)
R.end()
