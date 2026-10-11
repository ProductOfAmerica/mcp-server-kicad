"""The GUI reading is not part of this repository.

The pressure test judged every write twice: kicad-cli's netlist of the file as written (the cli
reading) and the netlist of the same project after an emulation of the GUI's load-time cleanup (the
gui reading). That emulation was a step-by-step port of KiCad's C++ source (SCHEMATIC::CleanUp,
BreakSegmentsOnJunctions and SCH_LINE::MergeOverlap). KiCad's source is GPL and this repository is
MIT, so whether to add such a port is the maintainer's decision, and it is not copied here.

This stub reports the reading as not available. judge3 skips it, records say so (gui_wrong is null,
never false), and gate.py prints wrong_gui as null with readings ["cli"], never 0 and never clean.
A maintainer who adds a real emulation replaces this module (AVAILABLE = True and a working
gui_load_proxy); nothing else changes.
"""

AVAILABLE = False
REASON = (
    "the GUI-load emulation is not part of this repository (it is a port of KiCad's GPL C++ load "
    "cleanup, and adding it is a maintainer decision)"
)


class GuiReadingUnavailable(RuntimeError):
    """Raised by anything that needs the GUI reading when it is not available."""


def gui_load_proxy(src, dst):
    """Write the GUI-load view of the schematic `src` to `dst`. Not available here."""
    raise GuiReadingUnavailable(REASON)
