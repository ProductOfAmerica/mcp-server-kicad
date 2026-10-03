"""Edge-case tests for KiCad MCP tools: duplicates, bad paths, odd rotations, extremes."""

from pathlib import Path

import pytest
from conftest import KICAD_SYM_VERSION, build_r_symbol, new_schematic, reparse
from kiutils.symbol import Symbol, SymbolLib
from mcp.server.mcpserver.exceptions import ToolError

from mcp_server_kicad import _shared, schematic
from mcp_server_kicad.schematic import _get_page_size


def _files(root: Path) -> dict[str, bytes]:
    """Every file under *root*, by relative path, to prove a refusal wrote nothing."""
    return {
        str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()
    }


def _derived_stub(name: str, parent: str, nickname: str | None = None) -> Symbol:
    """A symbol that only extends *parent*, the shape of a derived stock symbol."""
    stub = Symbol()
    stub.entryName = name
    stub.libraryNickname = nickname
    stub.extends = parent
    return stub


class TestDuplicateReference:
    """This used to assert a duplicate reference was fine, on a false premise.

    Its docstring read "KiCad flags duplicate references via ERC, not at
    placement time." Measured 2026-08-12: `kicad-cli sch erc` reports no
    duplicate-reference violation at all, not even for a resistor and a
    capacitor both called R1. Nothing downstream catches it, so placement time
    is the only time it can be caught.

    A user hit this for real: two R1 symbols stacked at the same coordinates,
    which ERC could not even report as unconnected pins because the coincident
    pins counted as connected to each other.
    """

    def test_duplicate_reference_is_refused(self, scratch_sch: Path) -> None:
        before = scratch_sch.read_bytes()
        with pytest.raises(ToolError, match="already placed"):
            schematic.place_component(
                lib_id="Device:R",
                reference="R1",  # scratch_sch already has R1
                value="4.7K",
                x=200,
                y=200,
                schematic_path=str(scratch_sch),
                project_path=str(scratch_sch.with_suffix(".kicad_pro")),
            )
        assert scratch_sch.read_bytes() == before, "a refused placement still wrote"

    def test_a_free_reference_still_places(self, scratch_sch: Path) -> None:
        """The guard must not block the ordinary case."""
        result = schematic.place_component(
            lib_id="Device:R",
            reference="R2",
            value="4.7K",
            x=200,
            y=200,
            schematic_path=str(scratch_sch),
            project_path=str(scratch_sch.with_suffix(".kicad_pro")),
        )
        assert "Placed" in result
        sch = reparse(scratch_sch)
        refs = {p.value for s in sch.schematicSymbols for p in s.properties if p.key == "Reference"}
        assert {"R1", "R2"} <= refs


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


class TestPlacementNeedsADefinition:
    """A placed symbol is a part only if its definition is embedded with it.

    KiCad keeps a part's body and pins in the schematic's lib_symbols, and the
    placed instance only points at them. Measured 2026-10-02: with no stock
    library to copy from, place_component("Device:R") reported "Placed R1",
    wrote no definition, and `kicad-cli sch erc` returned 0 with no violations,
    because a symbol with no pins gives ERC nothing to check. A derived stock
    symbol, copied verbatim, made kicad-cli refuse to load the schematic at all.
    As with a duplicate reference, placement is the only time this can be
    caught, so every case here must refuse and leave every file as it was.
    """

    @pytest.mark.parametrize(
        ("tool", "args"),
        [
            ("place_component", {"lib_id": "Device:R", "reference": "R1", "value": "10K"}),
            ("add_power_symbol", {"lib_id": "power:VCC", "reference": "#PWR01"}),
            (
                "auto_place_decoupling_cap",
                {
                    "lib_id": "Device:C",
                    "reference": "C1",
                    "value": "100nF",
                    "power_net": "VCC",
                    "ground_net": "GND",
                },
            ),
        ],
    )
    def test_no_stock_library_refuses_through_every_placing_tool(
        self, tool, args, empty_sch: Path, tmp_path: Path, monkeypatch
    ) -> None:
        # The KiCad-free host this used to orphan symbols on.
        monkeypatch.setattr(schematic, "_resolve_system_lib", lambda _prefix: None)
        # A .kicad_pro sibling makes a placement write the root index too.
        pro = empty_sch.with_suffix(".kicad_pro")
        pro.write_text("{}")
        before = _files(tmp_path)
        with pytest.raises(ToolError, match=r"no \w+\.kicad_sym in"):
            getattr(schematic, tool)(
                x=100, y=100, schematic_path=str(empty_sch), project_path=str(pro), **args
            )
        assert _files(tmp_path) == before, "a refused placement still wrote"

    def test_an_unknown_library_names_every_folder_searched(
        self, empty_sch: Path, stock_symbol_dir: Path, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setenv("KICAD_SYMBOL_DIR", str(stock_symbol_dir))
        before = _files(tmp_path)
        with pytest.raises(ToolError) as exc:
            schematic.place_component(
                lib_id="NoSuchLib:R",
                reference="R1",
                value="1K",
                x=100,
                y=100,
                schematic_path=str(empty_sch),
            )
        msg = str(exc.value)
        assert "no NoSuchLib.kicad_sym in" in msg
        # The override is listed, and listed ahead of the standard folders,
        # because that is the order the search runs in.
        assert msg.index(str(stock_symbol_dir)) < msg.index(str(_shared._SYSTEM_SYM_DIRS[0]))
        assert "symbol_lib_path" in msg and "sym-lib-table" in msg
        assert _files(tmp_path) == before

    def test_a_bare_lib_id_says_where_a_definition_could_come_from(
        self, empty_sch: Path, tmp_path: Path
    ) -> None:
        before = _files(tmp_path)
        with pytest.raises(ToolError) as exc:
            schematic.place_component(
                lib_id="R",
                reference="R1",
                value="1K",
                x=100,
                y=100,
                schematic_path=str(empty_sch),
            )
        msg = str(exc.value)
        assert "without a library prefix" in msg
        assert "symbol_lib_path" in msg and "add_lib_symbol" in msg
        assert _files(tmp_path) == before

    def test_a_library_without_the_symbol_is_named_by_its_file(
        self, empty_sch: Path, scratch_sym_lib: Path, tmp_path: Path
    ) -> None:
        """A bare lib_id has no prefix to call the library by, so name the file.

        "Zz" is close to nothing in the library (only TestPart), so the message
        falls back to the list_lib_symbols hint rather than a "Similar:" list.
        """
        before = _files(tmp_path)
        with pytest.raises(ToolError) as exc:
            schematic.place_component(
                lib_id="Zz",
                reference="U1",
                value="X",
                x=100,
                y=100,
                symbol_lib_path=str(scratch_sym_lib),
                schematic_path=str(empty_sch),
            )
        msg = str(exc.value)
        assert str(scratch_sym_lib) in msg and "list_lib_symbols" in msg
        assert "Zz library" not in msg
        assert _files(tmp_path) == before

    def test_a_stock_library_without_the_symbol_is_named(
        self, empty_sch: Path, stock_symbol_dir: Path, tmp_path: Path, monkeypatch
    ) -> None:
        """The deterministic twin of the HAS_KICAD_LIBS-gated suggestion test."""
        monkeypatch.setenv("KICAD_SYMBOL_DIR", str(stock_symbol_dir))
        before = _files(tmp_path)
        with pytest.raises(ToolError, match="not found in Device library") as exc:
            schematic.place_component(
                lib_id="Device:C",
                reference="C1",
                value="1u",
                x=100,
                y=100,
                schematic_path=str(empty_sch),
            )
        assert str(stock_symbol_dir / "Device.kicad_sym") in str(exc.value)
        assert _files(tmp_path) == before

    def test_a_missing_library_file_is_a_tool_error(self, empty_sch: Path, tmp_path: Path) -> None:
        before = _files(tmp_path)
        with pytest.raises(ToolError, match="symbol library not found"):
            schematic.place_component(
                lib_id="X:R",
                reference="R1",
                value="1K",
                x=100,
                y=100,
                symbol_lib_path=str(tmp_path / "absent.kicad_sym"),
                schematic_path=str(empty_sch),
            )
        assert _files(tmp_path) == before

    def test_a_schematic_is_not_a_symbol_library(
        self, empty_sch: Path, kicad_native_sch: Path, tmp_path: Path
    ) -> None:
        before = _files(tmp_path)
        with pytest.raises(ToolError, match="not a KiCad symbol library"):
            schematic.place_component(
                lib_id="X:R",
                reference="R1",
                value="1K",
                x=100,
                y=100,
                symbol_lib_path=str(kicad_native_sch),
                schematic_path=str(empty_sch),
            )
        assert _files(tmp_path) == before

    def test_a_derived_symbol_is_refused_and_its_parent_named(
        self, empty_sch: Path, tmp_path: Path
    ) -> None:
        lib = SymbolLib(version=KICAD_SYM_VERSION, generator="kicad_symbol_editor")
        lib.symbols.append(build_r_symbol())
        lib.symbols.append(_derived_stub("R_Derived", "R"))
        lib.filePath = str(tmp_path / "Derived.kicad_sym")
        lib.to_file()
        before = _files(tmp_path)
        with pytest.raises(ToolError, match="derived symbol") as exc:
            schematic.place_component(
                lib_id="Lib:R_Derived",
                reference="R1",
                value="1K",
                x=100,
                y=100,
                symbol_lib_path=lib.filePath,
                schematic_path=str(empty_sch),
            )
        assert "extends 'R'" in str(exc.value)
        assert _files(tmp_path) == before
        # The parent places, pins and all, so the refusal is not broader than the problem.
        assert "Placed R1" in schematic.place_component(
            lib_id="Lib:R",
            reference="R1",
            value="1K",
            x=100,
            y=100,
            symbol_lib_path=lib.filePath,
            schematic_path=str(empty_sch),
        )
        (r1,) = reparse(empty_sch).schematicSymbols
        assert r1.libId == "Lib:R" and set(r1.pins) == {"1", "2"}

    # The schematic carrying the stub is the subject: KiCad refuses to load it.
    @pytest.mark.no_kicad_validation
    def test_a_derived_stub_already_embedded_is_refused(self, tmp_path: Path) -> None:
        """A stub an older version wrote must not be placed again."""
        sch = new_schematic()
        sch.libSymbols.append(_derived_stub("R_Derived", "R", nickname="Lib"))
        path = tmp_path / "stub.kicad_sch"
        sch.filePath = str(path)
        sch.to_file()
        before = _files(tmp_path)
        with pytest.raises(ToolError, match="derived symbol") as exc:
            schematic.place_component(
                lib_id="Lib:R_Derived",
                reference="R1",
                value="1K",
                x=100,
                y=100,
                schematic_path=str(path),
            )
        assert "'Lib:R'" in str(exc.value)
        assert _files(tmp_path) == before


class TestBadPaths:
    def test_nonexistent_schematic(self) -> None:
        """list_schematic_components on a nonexistent file should raise an Exception."""
        with pytest.raises(Exception):
            schematic.list_schematic_components("/nonexistent/path.kicad_sch")

    def test_nonexistent_sym_lib(self, scratch_sch: Path) -> None:
        """add_lib_symbol with a nonexistent library path is a ToolError naming it.

        This accepted any Exception, which let a raw FileNotFoundError through.
        """
        with pytest.raises(ToolError, match="symbol library not found"):
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

    def test_resize_then_place(self, empty_sch: Path, stock_symbol_dir: Path, monkeypatch) -> None:
        """Placement outside A4 fails, but succeeds after resizing to A3."""
        # Device:R from the stand-in stock folder, so this does not need KiCad.
        monkeypatch.setenv("KICAD_SYMBOL_DIR", str(stock_symbol_dir))
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
