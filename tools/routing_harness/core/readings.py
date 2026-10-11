"""Which readings a run took, and the verdict helpers every summary shares.

A record carries cli_wrong and gui_wrong: True (wrong), False (judged clean) or None (not judged).
A reading that was not taken is skipped: it is never counted clean and never counted unjudged.
Without the GUI reading (core/gui_reading.py) the readings are ("cli",) and a record is clean when
its cli reading is, so every summary that says "clean in both readings" must print LABEL beside
the number: it is a statement about the cli reading only.

No package import here, so summarizers need not load the judge.
"""

from gui_reading import AVAILABLE as GUI_AVAILABLE

READINGS = ("cli", "gui") if GUI_AVAILABLE else ("cli",)
LABEL = "cli and gui readings" if GUI_AVAILABLE else "cli reading only (gui reading not available)"


def clean(r) -> bool:
    """Every reading that was taken judged this record clean (False means judged and not wrong)."""
    return all(r.get(f"{rd}_wrong") is False for rd in READINGS)


def wrong(r) -> bool:
    """Any reading judged this record wrong."""
    return any(r.get(k) for k in ("cli_wrong", "gui_wrong"))


def unjudged(r) -> bool:
    """A reading that was taken left this record unjudged (None)."""
    return any(r.get(f"{rd}_wrong") is None for rd in READINGS)
