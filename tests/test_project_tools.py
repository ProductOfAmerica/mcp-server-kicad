"""Tests for project scaffolding tools."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mcp_server_kicad import project


class TestCreateProject:
    def test_creates_pro_and_prl(self, tmp_path: Path):
        result = project.create_project(directory=str(tmp_path / "myproj"), name="myproj")
        assert "myproj.kicad_pro" in result

        pro = tmp_path / "myproj" / "myproj.kicad_pro"
        prl = tmp_path / "myproj" / "myproj.kicad_prl"
        assert pro.exists()
        assert prl.exists()

        pro_data = json.loads(pro.read_text())
        assert pro_data["meta"]["filename"] == "myproj.kicad_pro"
        assert pro_data["meta"]["version"] == 1

        prl_data = json.loads(prl.read_text())
        assert prl_data["meta"]["filename"] == "myproj.kicad_prl"
        assert prl_data["meta"]["version"] == 3

    def test_creates_directory_if_missing(self, tmp_path: Path):
        target = tmp_path / "deep" / "nested" / "proj"
        project.create_project(directory=str(target), name="test")
        assert (target / "test.kicad_pro").exists()

    def test_errors_if_pro_exists(self, tmp_path: Path):
        (tmp_path / "dup.kicad_pro").write_text("{}")
        result = project.create_project(directory=str(tmp_path), name="dup")
        assert "already exists" in result
