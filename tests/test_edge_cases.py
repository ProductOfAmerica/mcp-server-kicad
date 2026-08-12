"""Edge-case tests for KiCad MCP tools: duplicates, bad paths, odd rotations, extremes."""

from pathlib import Path

import pytest
from conftest import reparse
from mcp.server.mcpserver.exceptions import ToolError

from mcp_server_kicad import schematic
from mcp_server_kicad.schematic import _get_page_size


class TestDuplicateReference:
    def test_duplicate_ref_allowed(self, scratch_sch: Path) -> None:
        """Placing a second component with the same reference (R1) should succeed.

        KiCad flags duplicate references via ERC, not at placement time.
        """
        result = schematic.place_component(
            lib_id="Device:R",
            reference="R1",
            value="4.7K",
            x=200,
            y=200,
            schematic_path=str(scratch_sch),
            project_path=str(scratch_sch.with_suffix(".kicad_pro")),
        )
        assert "Placed" in result

        sch = reparse(scratch_sch)
        r1_syms = [
            s
            for s in sch.schematicSymbols
            if any(p.key == "Reference" and p.value == "R1" for p in s.properties)
        ]
        assert len(r1_syms) == 2


class TestInvalidRotation:
    """These two used to assert the opposite, under no_kicad_validation.

    They placed the symbol, checked the illegal angle had been written, and
    suppressed the kicad-cli oracle so nothing noticed. Measured 2026-08-12:
    the resulting schematic makes kicad-cli fail outright with "Failed to load
    schematic". The oracle was not merely unaimed here, it was switched off so
    an unloadable file could pass.
    """

    @pytest.mark.parametrize("rotation", [45, -90, 37, 359])
    def test_non_orthogonal_rotation_is_refused(self, scratch_sch: Path, rotation) -> None:
        before = scratch_sch.read_bytes()
        with pytest.raises(ToolError, match="not valid in a schematic"):
            schematic.place_component(
                lib_id="Device:R",
                reference="R2",
                value="1K",
                x=150,
                y=150,
                rotation=rotation,  # type: ignore[arg-type]
                schematic_path=str(scratch_sch),
                project_path=str(scratch_sch.with_suffix(".kicad_pro")),
            )
        assert scratch_sch.read_bytes() == before, "a refused edit still touched the file"

    @pytest.mark.parametrize("rotation", [0, 90, 180, 270])
    def test_the_four_legal_angles_still_place(self, scratch_sch: Path, rotation) -> None:
        result = schematic.place_component(
            lib_id="Device:R",
            reference="R2",
            value="1K",
            x=150,
            y=150,
            rotation=rotation,
            schematic_path=str(scratch_sch),
            project_path=str(scratch_sch.with_suffix(".kicad_pro")),
        )
        assert "Placed" in result
        sch = reparse(scratch_sch)
        r2 = next(
            s
            for s in sch.schematicSymbols
            if any(p.key == "Reference" and p.value == "R2" for p in s.properties)
        )
        assert r2.position.angle == rotation


class TestBadPaths:
    def test_nonexistent_schematic(self) -> None:
        """list_schematic_components on a nonexistent file should raise an Exception."""
        with pytest.raises(Exception):
            schematic.list_schematic_components("/nonexistent/path.kicad_sch")

    def test_nonexistent_sym_lib(self, scratch_sch: Path) -> None:
        """add_lib_symbol with a nonexistent library path should raise an Exception."""
        with pytest.raises(Exception):
            schematic.add_lib_symbol("/nonexistent/lib.kicad_sym", "X", str(scratch_sch))


class TestLargeCoordinates:
    def test_extreme_position(self, scratch_sch: Path) -> None:
        """Placing a component at extreme coordinates should round-trip correctly.

        Coordinates outside the page boundary are rejected.
        """
        with pytest.raises(ToolError, match="outside"):
            schematic.place_component(
                lib_id="Device:R",
                reference="R99",
                value="100K",
                x=99999.8,
                y=99999.8,
                schematic_path=str(scratch_sch),
                project_path=str(scratch_sch.with_suffix(".kicad_pro")),
            )


class TestSetPageSize:
    def test_set_standard_size_a3(self, scratch_sch: Path) -> None:
        """Setting page size to A3 should round-trip correctly."""
        result = schematic.set_page_size(
            size="A3",
            schematic_path=str(scratch_sch),
        )
        assert "Page size set" in result

        sch = reparse(scratch_sch)
        assert sch.paper.paperSize == "A3"

    def test_set_user_custom_size(self, scratch_sch: Path) -> None:
        """Setting a custom 'User' page size stores width and height."""
        result = schematic.set_page_size(
            size="User",
            width=500,
            height=300,
            schematic_path=str(scratch_sch),
        )
        assert "Page size set" in result

        sch = reparse(scratch_sch)
        assert sch.paper.paperSize == "User"
        assert sch.paper.width == 500
        assert sch.paper.height == 300

    def test_user_without_dimensions_returns_error(self, scratch_sch: Path) -> None:
        """'User' size without width/height raises ToolError."""
        with pytest.raises(ToolError):
            schematic.set_page_size(
                size="User",
                schematic_path=str(scratch_sch),
            )

    def test_invalid_size_returns_error(self, scratch_sch: Path) -> None:
        """An invalid size name like 'Z99' raises ToolError."""
        with pytest.raises(ToolError):
            schematic.set_page_size(
                size="Z99",
                schematic_path=str(scratch_sch),
            )

    def test_resize_then_place(self, empty_sch: Path) -> None:
        """Placement outside A4 fails, but succeeds after resizing to A3."""
        # A4 is 297x210 — (400, 200) is outside
        with pytest.raises(ToolError, match="outside"):
            schematic.place_component(
                lib_id="Device:R",
                reference="R1",
                value="10K",
                x=400,
                y=200,
                schematic_path=str(empty_sch),
                project_path=str(empty_sch.with_suffix(".kicad_pro")),
            )

        # Resize to A3 (420x297) — (400, 200) is now inside
        result = schematic.set_page_size(
            size="A3",
            schematic_path=str(empty_sch),
        )
        assert "Page size set" in result

        # Place should now succeed
        result = schematic.place_component(
            lib_id="Device:R",
            reference="R1",
            value="10K",
            x=400,
            y=200,
            schematic_path=str(empty_sch),
            project_path=str(empty_sch.with_suffix(".kicad_pro")),
        )
        assert "Placed" in result

    def test_portrait_mode(self, empty_sch: Path) -> None:
        """A4 portrait should swap dimensions: 210x297 instead of 297x210."""
        result = schematic.set_page_size(
            size="A4",
            portrait=True,
            schematic_path=str(empty_sch),
        )
        assert "Page size set" in result

        sch = reparse(empty_sch)
        w, h = _get_page_size(sch)
        # Normal A4: 297x210; portrait swaps to 210x297
        assert w == 210
        assert h == 297
