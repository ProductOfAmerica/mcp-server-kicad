"""The s-expression files a KiCad install ships, for corpus tests.

KiCad's shared data lives under share/kicad on Windows and Linux and SharedSupport in
the macOS bundle, found from the resolved kicad-cli (_shared._kicad_root). ci.yml
installs no KiCad, so there is no corpus there; the macOS cask ships templates but no
demos. A corpus test skips when share_dirs() is empty and, where an install exists,
first requires that it found what it checks.

lexed() caches the lexer port's tree of one file per worker (a few files at a time),
so several tests over the same file lex it once. The tree is shared: never mutate it.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from kicad_ref import dsn_lexer as L

from mcp_server_kicad._shared import _kicad_root

#: The s-expression file suffixes KiCad writes.
SEXPR_SUFFIXES = (
    ".kicad_sch",
    ".kicad_pcb",
    ".kicad_sym",
    ".kicad_mod",
    ".kicad_wks",
    ".kicad_dru",
)
#: Library tables, which have no suffix.
TABLE_NAMES = ("sym-lib-table", "fp-lib-table")
#: Per push, corpus tests take files up to this size. Of KiCad 9.0.8's demos and
#: templates that leaves out five vme-wren sheets (1.31 to 1.50 MB), the seven boards
#: of 2 MB and more (2.02 to 71.6 MB) and four footprints in vme-wren's wren.pretty
#: (measured 2026-10-11). Their nightly siblings take every file.
PER_PUSH_CAP = 1_200_000
NO_INSTALL = (
    "no KiCad install found through _kicad_root() (ci.yml installs no KiCad;"
    " the macOS cask ships templates only)"
)


def share_dirs() -> list[Path]:
    """KiCad's shared data: share/kicad on Windows and Linux, SharedSupport on macOS."""
    root = _kicad_root()
    if root is None:
        return []
    return [d for d in (root / "share" / "kicad", root / "SharedSupport") if d.is_dir()]


def _matches(path: Path, suffixes, names) -> bool:
    return path.is_file() and (path.suffix in suffixes or path.name in names)


def shipped(
    subdirs=("demos", "template"),
    suffixes=SEXPR_SUFFIXES,
    names=TABLE_NAMES,
    cap: int | None = None,
) -> list[tuple[str, Path]]:
    """(name relative to its share directory, path) of every matching file under the
    given subdirectories of each share directory, up to ``cap`` bytes each, in a
    stable order. ``subdirs=None`` walks the whole share directory."""
    out = []
    for share in share_dirs():
        bases = [share] if subdirs is None else [share / sub for sub in subdirs]
        for base in bases:
            if not base.is_dir():
                continue
            for path in sorted(base.rglob("*")):
                if _matches(path, suffixes, names) and (cap is None or path.stat().st_size <= cap):
                    out.append((path.relative_to(share).as_posix(), path))
    return out


@lru_cache(maxsize=8)
def lexed(path: Path) -> list:
    """dsn_lexer.tree of the file at ``path``, with the bar as its own parser sets it
    (dsn_lexer.knows_bar_of). Raises the lexer's LexError for a file it refuses (bug 54:
    KiCad's RoyalBlue54L-Feather files)."""
    data = Path(path).read_bytes()
    return L.tree(data, knows_bar=L.knows_bar_of(data))
