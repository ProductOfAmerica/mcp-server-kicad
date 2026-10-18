"""kicad-cli's own reading of a project's sheets, as the judge of tests that place a file in it.

``kicad-cli sch export netlist`` names the file of every sheet instance of a project, by the
sheet's path as KiCad prints it (SCH_SHEET_PATH::PathHumanReadable). Imports nothing from
mcp_server_kicad. Fixtures name their sheet files uniquely, because the netlist prints only a
file's name.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from conftest import _cli_report


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
