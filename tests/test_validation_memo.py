"""Guard for the autouse ERC validation memo in conftest.

The memo skips ``kicad-cli`` on a schematic body it has already validated, keyed
on the file with UUIDs normalised away.  If that key ever collapsed two files
that differ in more than UUIDs, the suite would silently stop validating them.
"""

from conftest import _UUID_RE

_SCH = b'(kicad_sch (uuid "0123abcd-4567-89ef-0123-456789abcdef") (x 1))'


def _key(data: bytes) -> bytes:
    return _UUID_RE.sub(b"U", data)


def test_key_collapses_uuid_only_differences():
    assert _key(_SCH) == _key(_SCH.replace(b"0123abcd", b"fedcba98"))


def test_key_keeps_every_other_difference():
    assert _key(_SCH) != _key(_SCH.replace(b"(x 1)", b"(x 2)"))
    # A malformed UUID is a real difference: it must not normalise away.
    assert _key(_SCH) != _key(_SCH.replace(b"0123abcd", b"0123abc"))
