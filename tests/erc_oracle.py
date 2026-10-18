"""Which violations of a project's ERC belong to one sheet, by kicad-cli's own reading.

kicad-cli files an ERC violation under the sheet path of its main item, except that a few
kinds (a dangling label or wire) carry no sheet path and are filed under the root, whatever
sheet the item is on (eeschema/erc/erc_report.cpp:169-172 at 9.0.8, 193-196 at 10.0.7).
A sheet's own violations are therefore:

- the entries kicad-cli files under the sheet's paths, found by name: the netlist
  (``kicad-cli sch export netlist``) names the file of every sheet path, and the report
  names each entry by the same path (both are SCH_SHEET_PATH::PathHumanReadable); and
- the entries under the root whose main item lives in the sheet's file, found by the item's
  uuid in the bytes of the project's sheet files, refused (RuntimeError) when two files
  hold it.

It never reads a report's ``uuid_path`` and imports nothing from mcp_server_kicad, so it
shares no assumption with the filter it judges. Fixtures name their sheet files uniquely,
because the netlist prints only a file's name.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

from conftest import _cli_report

Key = tuple[str, str]


def sheet_files(root: Path) -> dict[str, str]:
    """{sheet path: file name} for every sheet of the project, from kicad-cli's netlist."""
    result, data = _cli_report(["sch", "export", "netlist", "--format", "kicadxml"], root, "n.xml")
    if data is None:
        raise RuntimeError(f"kicad-cli wrote no netlist (rc={result.returncode})")
    sheets = {}
    for sheet in ET.fromstring(data).iter("sheet"):
        source = sheet.find("title_block/source")
        sheets[sheet.get("name", "")] = "" if source is None else (source.text or "")
    return sheets


def erc_of(path: Path) -> dict:
    """kicad-cli's ERC report of the schematic at *path*, run on it as a root."""
    result, data = _cli_report(["sch", "erc", "--format", "json", "--severity-all"], path, "e.json")
    if data is None:
        raise RuntimeError(f"kicad-cli wrote no ERC report (rc={result.returncode})")
    return json.loads(data)


def violations_of(report: dict) -> list[dict]:
    """Every violation of a report, in the order kicad-cli lists them."""
    return [v for entry in report["sheets"] for v in entry["violations"]]


def key(violation: dict) -> Key:
    """(type, main item uuid): what makes two violations the same one."""
    items = violation.get("items") or []
    return violation["type"], items[0]["uuid"] if items else ""


def keys(violations: list[dict]) -> Counter[Key]:
    return Counter(key(v) for v in violations)


class Project:
    """kicad-cli's ERC report and netlist of one project, read once."""

    def __init__(self, root: Path):
        self.root = root
        self.report = erc_of(root)
        self.names = sheet_files(root)
        self._bytes: dict[str, bytes] = {}
        for path in sorted(root.parent.rglob("*.kicad_sch")):
            if path.name in self._bytes:
                raise RuntimeError(
                    f"two sheet files are called {path.name}: the netlist cannot tell"
                )
            self._bytes[path.name] = path.read_bytes()

    def everything(self) -> list[dict]:
        """Every violation of the root report: what a run on the root returns."""
        return violations_of(self.report)

    def _file_of(self, violation: dict) -> str | None:
        """The one sheet file that holds the violation's main item, or None when none does."""
        _, uuid = key(violation)
        if not uuid:
            return None
        pattern = re.compile(rb'\(uuid\s+"?' + re.escape(uuid.encode()) + rb'"?\s*\)')
        holders = [name for name, data in self._bytes.items() if pattern.search(data)]
        if len(holders) > 1:
            raise RuntimeError(f"item {uuid} is in {holders}: a sheet cannot be told from another")
        return holders[0] if holders else None

    def own(self, sheet: Path) -> list[dict]:
        """The violations that are *sheet*'s own, every instance of it included."""
        mine = []
        for index, entry in enumerate(self.report["sheets"]):
            if index == 0:  # the root's entry: the root's own items, and every item with no path
                mine += [v for v in entry["violations"] if self._file_of(v) == sheet.name]
            elif self.names.get(entry["path"]) == sheet.name:
                mine += entry["violations"]
        return mine
