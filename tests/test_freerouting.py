"""Tests for Freerouting helper module."""

import hashlib
import io
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path
from unittest.mock import patch

import pcbnew_judge as PJ
import pytest
from conftest import requires_cli
from kicad_ref import board_item as B
from kiutils.board import Board
from kiutils.footprint import Footprint
from kiutils.items.common import Net, Position, Property
from kiutils.items.zones import Hatch, KeepoutSettings, Zone, ZonePolygon
from mcp.server.mcpserver.exceptions import ToolError

import mcp_server_kicad._freerouting as _fr_module
from mcp_server_kicad import _cst
from mcp_server_kicad._freerouting import (
    check_java,
    ensure_jar,
    export_dsn,
    find_jar,
    find_pcbnew_python,
    import_ses,
    jar_java_requirement,
    pcbnew_major,
    run_freerouting,
    wx_app_prelude,
)
from mcp_server_kicad._shared import _keepout_dict, _xy
from mcp_server_kicad.pcb import _promote_footprint_keepouts, autoroute_pcb, run_drc


class TestCheckJava:
    def test_java_found_valid_version(self):
        mock_result = subprocess.CompletedProcess(
            args=["java", "-version"],
            returncode=0,
            stdout="",
            stderr='openjdk version "25.0.2" 2026-01-20',
        )
        with patch("subprocess.run", return_value=mock_result) as run:
            result = check_java(java="/fake/java")
            assert result is None
        # The binary the caller resolved, never a bare name to be searched for.
        assert run.call_args.args[0][0] == "/fake/java"

    def test_java_not_found(self):
        with patch("subprocess.run", side_effect=FileNotFoundError):
            result = check_java(java="/fake/java")
            assert result is not None
            assert "Java" in result
            assert "https://adoptium.net/temurin/releases/" in result
            # It named `apt install default-jre`, which can install a Java older
            # than the jar needs.
            assert "default-jre" not in result

    def test_no_java_on_path_is_reported_without_spawning(self):
        """None is what the PATH lookup hands over when there is no java at all."""
        with patch("subprocess.run") as run:
            result = check_java(java=None)
        assert result is not None and "Java runtime not found" in result
        run.assert_not_called()

    def test_java_too_old(self):
        mock_result = subprocess.CompletedProcess(
            args=["java", "-version"],
            returncode=0,
            stdout="",
            stderr='openjdk version "11.0.2" 2019-01-15',
        )
        with patch("subprocess.run", return_value=mock_result):
            result = check_java(java="/fake/java")
            assert result is not None
            assert f"Java {_fr_module._JAVA_FLOOR}+" in result


class TestFindJar:
    def test_env_var_override(self, tmp_path):
        jar = tmp_path / "custom.jar"
        jar.touch()
        with patch.dict(os.environ, {"FREEROUTING_JAR": str(jar)}):
            assert find_jar() == str(jar)

    def test_env_var_missing_file(self, tmp_path):
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        with (
            patch.dict(os.environ, {"FREEROUTING_JAR": "/nonexistent/fr.jar"}),
            patch("mcp_server_kicad._freerouting._cache_dir", return_value=empty_dir),
        ):
            assert find_jar() is None

    def test_no_jar(self, tmp_path):
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        with (
            patch.dict(os.environ, {"FREEROUTING_JAR": ""}, clear=False),
            patch("mcp_server_kicad._freerouting._cache_dir", return_value=empty_dir),
        ):
            assert find_jar() is None

    def test_a_relative_env_var_is_not_resolved_against_the_cwd(self, tmp_path, monkeypatch):
        """java -jar loaded whatever freerouting.jar sat in the server's working directory."""
        (tmp_path / "freerouting.jar").touch()
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("FREEROUTING_JAR", "freerouting.jar")
        monkeypatch.setattr(_fr_module, "_cache_dir", lambda: tmp_path / "empty")
        assert find_jar() is None


class TestEnsureJar:
    def test_already_exists(self, tmp_path):
        jar = tmp_path / "freerouting.jar"
        jar.touch()
        with patch("mcp_server_kicad._freerouting.find_jar", return_value=str(jar)):
            path, err = ensure_jar()
            assert path == str(jar)
            assert err is None

    def test_download_success(self, tmp_path):
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        jar_path = str(cache_dir / "freerouting.jar")

        with (
            patch("mcp_server_kicad._freerouting.find_jar", return_value=None),
            patch("mcp_server_kicad._freerouting._download_jar", return_value=jar_path),
        ):
            path, err = ensure_jar()
            assert path == jar_path
            assert err is None

    def test_download_failure(self, tmp_path):
        with (
            patch("mcp_server_kicad._freerouting.find_jar", return_value=None),
            patch(
                "mcp_server_kicad._freerouting._download_jar",
                side_effect=RuntimeError("Network error"),
            ),
        ):
            path, err = ensure_jar()
            assert path is None
            assert err is not None
            assert "Network error" in err

    def test_a_relative_env_var_is_reported_not_used(self, tmp_path, monkeypatch):
        """Ignored like KICAD_CLI_PATH's, so the message has to say what it needs."""
        (tmp_path / "freerouting.jar").touch()
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("FREEROUTING_JAR", "freerouting.jar")
        monkeypatch.setattr(_fr_module, "_cache_dir", lambda: tmp_path / "empty")
        with patch(
            "mcp_server_kicad._freerouting._download_jar", side_effect=RuntimeError("offline")
        ):
            path, err = ensure_jar()
        assert path is None
        assert err is not None and "absolute path" in err


class TestPinnedJar:
    """The download is one release asset, checked against its pinned SHA-256
    before it is cached or run.

    Nothing here touches the network: urlopen is faked, and where the right
    bytes are needed the pin is pointed at a small stand-in jar's digest,
    because the real jar is 65 MB. Every flow also checks the URLs it
    requested, so a path that asked for anything but the pinned asset fails.
    """

    @pytest.fixture
    def cache(self, tmp_path, monkeypatch):
        """An existing, empty cache directory, so a write would land."""
        cache = tmp_path / "cache"
        cache.mkdir()
        monkeypatch.setattr(_fr_module, "_cache_dir", lambda: cache)
        monkeypatch.delenv("FREEROUTING_JAR", raising=False)
        return cache

    @pytest.fixture
    def stand_in(self, tmp_path, monkeypatch):
        """A small jar's bytes, with the pin pointed at their digest."""
        jar = Path(TestJarJavaRequirement._jar(tmp_path, 69)).read_bytes()
        monkeypatch.setattr(_fr_module, "_FREEROUTING_SHA256", hashlib.sha256(jar).hexdigest())
        return jar

    @pytest.fixture
    def serve(self, monkeypatch):
        """serve(body) makes urlopen answer with *body*; returns the URLs requested."""
        requested: list[str] = []

        def serve(body: bytes) -> list[str]:
            def fake_urlopen(url, *args, **kwargs):
                requested.append(url)
                return io.BytesIO(body)

            monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
            return requested

        return serve

    def _cached(self, cache: Path) -> Path:
        return cache / f"freerouting-{_fr_module._FREEROUTING_VERSION}.jar"

    def test_a_hash_mismatch_is_refused_and_nothing_is_cached(self, cache, serve):
        requested = serve(b"PK\x03\x04 not the pinned jar")
        path, err = ensure_jar()
        assert path is None
        assert err is not None
        assert "SHA-256" in err and "neither saved nor run" in err
        assert "FREEROUTING_JAR" in err and "absolute path" in err
        # No jar, and no temp file either.
        assert list(cache.iterdir()) == []
        assert requested == [_fr_module._FREEROUTING_URL]

    def test_the_pinned_bytes_are_accepted_and_cached(self, cache, stand_in, serve):
        requested = serve(stand_in)
        path, err = ensure_jar()
        assert (path, err) == (str(self._cached(cache)), None)
        assert self._cached(cache).read_bytes() == stand_in
        assert list(cache.iterdir()) == [self._cached(cache)]
        # Served from the cache the second time.
        assert ensure_jar() == (path, None)
        assert requested == [_fr_module._FREEROUTING_URL]

    def test_a_stale_unpinned_jar_is_not_used(self, cache, stand_in, serve):
        """Releases before the pin cached freerouting.jar from whatever release
        was newest. That it exists is no reason to run it."""
        (cache / "freerouting.jar").write_bytes(b"PK\x03\x04 an unpinned build")
        assert find_jar() is None
        requested = serve(stand_in)
        assert ensure_jar() == (str(self._cached(cache)), None)
        assert requested == [_fr_module._FREEROUTING_URL]

    def test_a_tampered_cached_jar_is_not_used(self, cache, stand_in, serve):
        """The pinned name is no proof of the pinned bytes."""
        cached = self._cached(cache)
        tampered = stand_in + b"appended after the download"
        cached.write_bytes(tampered)
        assert find_jar() is None

        requested = serve(b"not the pinned jar either")
        path, err = ensure_jar()
        assert path is None and err is not None
        assert cached.read_bytes() == tampered  # the refused download wrote nothing

        serve(stand_in)
        assert ensure_jar() == (str(cached), None)
        assert cached.read_bytes() == stand_in
        assert requested == [_fr_module._FREEROUTING_URL] * 2

    def test_freerouting_jar_is_used_as_given(self, cache, tmp_path, serve, monkeypatch):
        """The user's own jar: no hash check, and no download."""
        own = tmp_path / "my-freerouting.jar"
        own.write_bytes(b"whichever build the user chose")
        monkeypatch.setenv("FREEROUTING_JAR", str(own))
        requested = serve(b"")
        assert ensure_jar() == (str(own), None)
        assert requested == []

    def test_no_code_path_requests_the_latest_release(self):
        """The pin names one release tag and its asset, and nothing in the
        package asks for the newest release any more."""
        match = re.fullmatch(
            r"https://github\.com/freerouting/freerouting/releases/download/"
            r"v(\d+\.\d+\.\d+)/freerouting-(\d+\.\d+\.\d+)\.jar",
            _fr_module._FREEROUTING_URL,
        )
        assert match is not None, _fr_module._FREEROUTING_URL
        assert match.group(1) == match.group(2) == _fr_module._FREEROUTING_VERSION
        package = Path(_fr_module.__file__).parent
        offenders = [
            p.name for p in package.rglob("*.py") if "releases/latest" in p.read_text("utf-8")
        ]
        assert offenders == []

    def test_the_suites_network_guard_reaches_the_download(self, cache, monkeypatch):
        """_freerouting imported urlopen by name, which kept the real function,
        so the autouse _no_network guard, which patches urllib.request, never
        covered the download (probed 2026-10-19). The URL is unreachable on
        purpose: were the guard bypassed again, this fails on a refused
        connection and fetches nothing."""
        monkeypatch.setattr(_fr_module, "_FREEROUTING_URL", "http://127.0.0.1:9/freerouting.jar")
        with pytest.raises(AssertionError, match="network connection"):
            _fr_module._download_jar()


class TestFindPcbnewPython:
    @pytest.fixture(autouse=True)
    def _reset_pcbnew_cache(self):
        _fr_module._pcbnew_cache = None
        yield
        _fr_module._pcbnew_cache = None

    def test_direct_import_works(self, monkeypatch):
        monkeypatch.delenv("KICAD_PYTHON", raising=False)
        mock_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with (
            patch("mcp_server_kicad._freerouting._kicad_python_candidates", return_value=[]),
            patch("mcp_server_kicad._freerouting._find_on_path", return_value="/abs/python3"),
            patch("subprocess.run", return_value=mock_result),
        ):
            python, env = find_pcbnew_python()
            assert python == "/abs/python3"

    def test_every_candidate_is_launched_by_absolute_path(self, tmp_path, monkeypatch):
        """A bare "python3" is searched for in the working directory first on Windows."""
        on_path = tmp_path / ("python3.exe" if os.name == "nt" else "python3")
        on_path.write_text("")
        on_path.chmod(0o755)
        monkeypatch.setenv("PATH", str(tmp_path))
        monkeypatch.delenv("KICAD_PYTHON", raising=False)
        launched = []

        def fail(args, **kwargs):
            launched.append(args[0])
            return subprocess.CompletedProcess(args=args, returncode=1, stdout="", stderr="")

        with patch("subprocess.run", side_effect=fail):
            assert find_pcbnew_python() == (None, None)
        assert str(on_path) in launched
        assert all(os.path.isabs(p) for p in launched), launched

    @pytest.mark.parametrize("value", ["python3", os.path.join("sub", "python3")])
    def test_a_relative_kicad_python_is_never_launched_as_given(self, value, tmp_path, monkeypatch):
        """Handed to subprocess unchanged, a bare name was searched for in the
        working directory before PATH on Windows, and "sub/python3" ran from it
        on every platform. Both are planted there, and neither may be launched."""
        exe = "python3.exe" if os.name == "nt" else "python3"
        cwd, bin_dir = tmp_path / "cwd", tmp_path / "bin"
        for planted in (cwd / exe, cwd / "sub" / exe, bin_dir / exe):
            planted.parent.mkdir(parents=True, exist_ok=True)
            planted.write_text("")
            planted.chmod(0o755)
        monkeypatch.chdir(cwd)
        monkeypatch.setenv("PATH", str(bin_dir))
        monkeypatch.setenv("KICAD_PYTHON", value)
        monkeypatch.setattr(_fr_module, "_kicad_python_candidates", lambda: [])
        launched = []

        def fail(args, **kwargs):
            launched.append(args[0])
            return subprocess.CompletedProcess(args=args, returncode=1, stdout="", stderr="")

        with patch("subprocess.run", side_effect=fail):
            assert find_pcbnew_python() == (None, None)
        assert launched and all(os.path.isabs(p) for p in launched), launched
        if value == "python3":
            # Resolved on PATH, not merely dropped.
            assert launched[0] == str(bin_dir / exe)

    @pytest.mark.parametrize(
        "value",
        [
            "python3",
            pytest.param(
                "python3.exe",
                marks=pytest.mark.skipif(os.name != "nt", reason="the .exe suffix is Windows'"),
            ),
        ],
    )
    def test_a_bare_kicad_python_is_cached_by_its_absolute_path(self, value, tmp_path, monkeypatch):
        """Every later launch (fill_zones, export_dsn, import_ses, pcbnew_major)
        takes the interpreter from the cache, so the cache has to hold the
        resolved path rather than the name."""
        on_path = tmp_path / ("python3.exe" if os.name == "nt" else "python3")
        on_path.write_text("")
        on_path.chmod(0o755)
        monkeypatch.setenv("PATH", str(tmp_path))
        monkeypatch.setenv("KICAD_PYTHON", value)
        monkeypatch.setattr(_fr_module, "_kicad_python_candidates", lambda: [])
        ok = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with patch("subprocess.run", return_value=ok):
            python, _env = find_pcbnew_python()
        assert python == str(on_path)
        assert _fr_module._pcbnew_cache is not None
        assert _fr_module._pcbnew_cache[0] == str(on_path)

    def test_no_pcbnew_available(self):
        with patch("subprocess.run", side_effect=Exception("fail")):
            python, env = find_pcbnew_python()
            assert python is None

    def test_pythonhome_stripped_from_probe_and_returned_env(self, tmp_path):
        """uv-trampoline venvs export PYTHONHOME, which breaks KiCad's own
        interpreter; the probe and the returned launch env must both drop it."""
        captured_envs = []
        # Absolute on every platform: "/fake/py" has no drive, and Windows
        # Python 3.13+ calls that relative, which KICAD_PYTHON now ignores.
        fake_py = str(tmp_path / "py")

        def fake_run(args, **kwargs):
            captured_envs.append(kwargs.get("env"))
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

        with patch.dict(os.environ, {"KICAD_PYTHON": fake_py, "PYTHONHOME": "/fake/venv"}):
            with patch("subprocess.run", side_effect=fake_run):
                python, env = find_pcbnew_python()

        assert python == fake_py
        assert env is not None
        assert "PYTHONHOME" not in env
        # KICAD_PYTHON pins a single probe, and it must run under the same
        # scrubbed dict the caller gets back.
        assert captured_envs == [env]


_FIND_PY = "mcp_server_kicad._freerouting.find_pcbnew_python"


class TestPcbnewMajor:
    @pytest.fixture(autouse=True)
    def _reset_major_cache(self):
        _fr_module._pcbnew_major_cache = None
        yield
        _fr_module._pcbnew_major_cache = None

    def _probe(self, **run_kwargs):
        with (
            patch(_FIND_PY, return_value=("python3", None)),
            patch("subprocess.run", **run_kwargs),
        ):
            return pcbnew_major()

    def test_parses_version_string(self):
        """The shape KiCad 9.0.8 actually prints, measured locally."""
        done = subprocess.CompletedProcess(args=[], returncode=0, stdout="9.0.8\n", stderr="")
        assert self._probe(return_value=done) == 9

    def test_unparseable_output(self):
        done = subprocess.CompletedProcess(args=[], returncode=0, stdout="no idea\n", stderr="")
        assert self._probe(return_value=done) is None

    def test_subprocess_raises(self):
        assert self._probe(side_effect=OSError("boom")) is None

    def test_no_interpreter(self):
        with patch(_FIND_PY, return_value=(None, None)):
            assert pcbnew_major() is None

    def test_real_pcbnew(self):
        """The cross-era proof, unmocked: pcbnew 9 on the Linux runner and
        pcbnew 10 on the gating macOS one both have to answer."""
        if find_pcbnew_python()[0] is None:
            pytest.skip("pcbnew Python bindings not available")
        major = pcbnew_major()
        assert isinstance(major, int)
        assert major >= 9


class TestChildScriptsIgnoreTheCwd:
    """No pcbnew child interpreter may import a module from the server's cwd.

    ``python -c`` puts ``''``, meaning the current directory, first on sys.path,
    and every child here is launched without a cwd of its own, so it inherits
    whichever directory the host started the server in. A pcbnew.py sitting
    there would run under KiCad's interpreter in place of KiCad's own module.

    These run real interpreters rather than scanning the source. The one used
    is the suite's own, which has neither pcbnew nor wx installed, so an import
    of pcbnew can only succeed by finding the planted module, and no wx assert
    dialog can be provoked.
    """

    @pytest.fixture
    def marker(self, tmp_path, monkeypatch):
        """Chdir into a directory whose pcbnew.py leaves this marker when imported."""
        cwd = tmp_path / "cwd"
        cwd.mkdir()
        marker = tmp_path / "imported-from-cwd"
        (cwd / "pcbnew.py").write_text(f"open({str(marker)!r}, 'w').close()\n", encoding="utf-8")
        monkeypatch.chdir(cwd)
        monkeypatch.setattr(_fr_module, "_pcbnew_cache", None)
        monkeypatch.setattr(_fr_module, "_pcbnew_major_cache", None)
        return marker

    def test_the_pcbnew_probes(self, marker, tmp_path, monkeypatch):
        """KICAD_PYTHON, the candidate loop, and the PYTHONPATH fallback loop."""
        monkeypatch.setenv("KICAD_PYTHON", sys.executable)
        monkeypatch.setattr(_fr_module, "_kicad_python_candidates", lambda: [sys.executable])
        # Any existing directory without a pcbnew in it, so the fallback loop runs.
        monkeypatch.setattr(_fr_module, "_KICAD_PYTHON_PATHS", [str(tmp_path)])
        find_pcbnew_python()
        assert not marker.exists(), "a probe imported pcbnew.py from the working directory"

    def test_the_version_probe(self, marker, monkeypatch):
        monkeypatch.setattr(_fr_module, "find_pcbnew_python", lambda: (sys.executable, None))
        pcbnew_major()
        assert not marker.exists(), "pcbnew_major imported pcbnew.py from the working directory"

    def test_the_wx_prelude(self, marker):
        """export_dsn, import_ses and fill_zones all start their scripts with it."""
        subprocess.run(
            [sys.executable, "-c", wx_app_prelude() + "import pcbnew"],
            capture_output=True,
            timeout=60,
        )
        assert not marker.exists(), "the prelude left the working directory on sys.path"


class TestExportDsn:
    def test_success(self, tmp_path):
        pcb_path = str(tmp_path / "board.kicad_pcb")
        dsn_path = str(tmp_path / "board.dsn")
        mock_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with (
            patch(_FIND_PY, return_value=("python3", None)),
            patch("subprocess.run", return_value=mock_result),
        ):
            err = export_dsn(pcb_path, dsn_path)
            assert err is None

    def test_pcbnew_not_found(self, tmp_path):
        with patch(_FIND_PY, return_value=(None, None)):
            err = export_dsn(str(tmp_path / "b.kicad_pcb"), str(tmp_path / "b.dsn"))
            assert err is not None
            assert "pcbnew" in err.lower() or "KiCad" in err


class TestImportSes:
    def test_success(self, tmp_path):
        pcb_path = str(tmp_path / "board.kicad_pcb")
        ses_path = str(tmp_path / "board.ses")
        out_path = str(tmp_path / "board_routed.kicad_pcb")
        mock_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with (
            patch(_FIND_PY, return_value=("python3", None)),
            patch("subprocess.run", return_value=mock_result),
        ):
            err = import_ses(pcb_path, ses_path, out_path)
            assert err is None

    def test_subprocess_fails(self, tmp_path):
        mock_result = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="pcbnew error"
        )
        with (
            patch(_FIND_PY, return_value=("python3", None)),
            patch("subprocess.run", return_value=mock_result),
        ):
            err = import_ses(
                str(tmp_path / "b.kicad_pcb"),
                str(tmp_path / "b.ses"),
                str(tmp_path / "b_routed.kicad_pcb"),
            )
            assert err is not None


class TestRunFreerouting:
    def test_success(self, tmp_path):
        dsn = tmp_path / "board.dsn"
        ses = tmp_path / "board.ses"
        dsn.touch()
        mock_result = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="Route complete", stderr=""
        )
        with patch("subprocess.run", return_value=mock_result) as run:
            err = run_freerouting(
                jar_path="/fake/freerouting.jar",
                dsn_path=str(dsn),
                ses_path=str(ses),
                java="/fake/java",
            )
            assert err is None
        # The same binary check_java approved, not "java" searched for afresh.
        assert run.call_args.args[0][0] == "/fake/java"

    def test_runs_headless_with_analytics_off(self, tmp_path):
        """The two settings Freerouting's docs/settings.md documents at v2.5.0, in
        its --section.property=value form: gui.enabled, whose default true opens a
        window on the user's desktop, and usage_and_diagnostic_data.disable_analytics,
        whose default false sends usage data. That the pinned jar accepts them was
        checked by routing a KiCad demo board with it on 2026-10-19: it logged that
        the GUI and analytics were off and warned of no unknown setting. This pins
        that they are passed."""
        ok = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with patch("subprocess.run", return_value=ok) as run:
            run_freerouting(
                jar_path="/fake/freerouting.jar",
                dsn_path=str(tmp_path / "board.dsn"),
                ses_path=str(tmp_path / "board.ses"),
                java="/fake/java",
            )
        argv = run.call_args.args[0]
        assert "--gui.enabled=false" in argv
        assert "--usage_and_diagnostic_data.disable_analytics=true" in argv

    def test_timeout(self, tmp_path):
        dsn = tmp_path / "board.dsn"
        dsn.touch()
        with patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="java", timeout=600),
        ):
            err = run_freerouting(
                jar_path="/fake/freerouting.jar",
                dsn_path=str(dsn),
                ses_path=str(tmp_path / "board.ses"),
                timeout=600,
                java="/fake/java",
            )
            assert err is not None
            assert "timeout" in err.lower() or "timed out" in err.lower()

    def test_nonzero_exit(self, tmp_path):
        dsn = tmp_path / "board.dsn"
        dsn.touch()
        mock_result = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="Error")
        with patch("subprocess.run", return_value=mock_result):
            err = run_freerouting(
                jar_path="/fake/freerouting.jar",
                dsn_path=str(dsn),
                ses_path=str(tmp_path / "board.ses"),
                java="/fake/java",
            )
            assert err is not None


# ---------------------------------------------------------------------------
# Helpers for _promote_footprint_keepouts tests
# ---------------------------------------------------------------------------


MM = 1_000_000


def _on_board(fp_x, fp_y, fp_angle, local):
    """Where KiCad stores a footprint zone's point that sits at ``local`` (mm) in the
    footprint's own frame, in mm: BOARD_ITEM::SetFPRelativePosition's arithmetic
    (kicad_ref.board_item: RotatePoint by the footprint's angle, then its position)."""
    x, y = B.fp_relative_to_board(
        (round(fp_x * MM), round(fp_y * MM)),
        fp_angle,
        (round(local[0] * MM), round(local[1] * MM)),
        major=9,
    )
    return x / MM, y / MM


def _make_board_with_fp_keepout(
    tmp_path, fp_angle=0, fp_layer="F.Cu", fp_x=100, fp_y=100, holes=()
):
    """Create a minimal board with one footprint owning a keepout zone.

    The zone is written where KiCad stores a footprint's zone: in board
    coordinates, since format( ZONE ) writes it without the footprint
    (pcbnew/pcb_io/kicad_sexpr/pcb_io_kicad_sexpr.cpp:2628-2637 at 9.0.8,
    3061-3074 at 10.0.7). Its outline is the square at (0, 0)-(10, 10) in the
    footprint's own frame, put on the board as KiCad puts a footprint-local
    point there (_on_board), whatever the footprint's side. Each entry in
    *holes* is a list of footprint-local (x, y) points, put on the board the
    same way and written as a further polygon of the zone, which KiCad reads
    as a hole in the outline.
    """
    board = Board.create_new()
    board.nets = [Net(number=0, name="")]

    fp = Footprint()
    fp.entryName = "TestPkg:TestFP"
    fp.layer = fp_layer
    fp.position = Position(X=fp_x, Y=fp_y, angle=fp_angle)
    fp.reference = Property(key="Reference", value="U1")
    fp.value = Property(key="Value", value="TEST")

    keepout_zone = Zone()
    keepout_zone.net = 0
    keepout_zone.netName = ""
    keepout_zone.layers = ["F.Cu", "B.Cu"]
    keepout_zone.hatch = Hatch(style="edge", pitch=0.5)
    keepout_zone.keepoutSettings = KeepoutSettings(
        tracks="not_allowed",
        vias="not_allowed",
        pads="not_allowed",
        copperpour="not_allowed",
        footprints="not_allowed",
    )
    poly = ZonePolygon()
    poly.coordinates = [
        Position(X=x, Y=y)
        for x, y in (
            _on_board(fp_x, fp_y, fp_angle, local) for local in ((0, 0), (10, 0), (10, 10), (0, 10))
        )
    ]
    keepout_zone.polygons = [poly]
    for hole in holes:
        cutout = ZonePolygon()
        cutout.coordinates = [
            Position(X=x, Y=y) for x, y in (_on_board(fp_x, fp_y, fp_angle, p) for p in hole)
        ]
        keepout_zone.polygons.append(cutout)
    fp.zones = [keepout_zone]

    board.footprints = [fp]
    pcb_path = str(tmp_path / "test.kicad_pcb")
    board.filePath = pcb_path
    board.to_file()
    return pcb_path


def _polygons(zone) -> list[list[tuple[float, float]]]:
    """A kiutils zone's polygons as (x, y) lists, in mm."""
    return [[(c.X, c.Y) for c in polygon.coordinates] for polygon in zone.polygons]


def _assert_promoted_as_stored(pcb_path, out_path) -> None:
    """The promoted board-level zone holds the footprint zone's points as stored, point
    for point, since both are board coordinates."""
    source = Board.from_file(pcb_path).footprints[0].zones[0]
    promoted = Board.from_file(out_path).zones[0]
    assert _polygons(promoted) == _polygons(source)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPromoteFootprintKeepouts:
    def test_happy_path(self, tmp_path):
        """A footprint's keepout is promoted with its points as stored.

        KiCad stores a footprint's zone in board coordinates: the FP at
        (100,100) with its square at (0,0)-(10,10) in its own frame stores
        (100,100),(110,100),(110,110),(100,110), and that is what the
        board-level copy must hold.
        """
        pcb_path = _make_board_with_fp_keepout(tmp_path, fp_x=100, fp_y=100)
        out_path = str(tmp_path / "out.kicad_pcb")

        count = _promote_footprint_keepouts(pcb_path, out_path)

        assert count == 1
        assert Path(out_path).exists()

        out_board = Board.from_file(out_path)
        assert len(out_board.zones) == 1
        coords = out_board.zones[0].polygons[0].coordinates
        xs = [round(c.X, 3) for c in coords]
        ys = [round(c.Y, 3) for c in coords]
        assert xs == [100.0, 110.0, 110.0, 100.0]
        assert ys == [100.0, 100.0, 110.0, 110.0]
        _assert_promoted_as_stored(pcb_path, out_path)

    def test_no_keepouts_returns_zero(self, tmp_path):
        """Board with no FP keepouts returns 0, no output file created."""
        board = Board.create_new()
        board.nets = [Net(number=0, name="")]
        fp = Footprint()
        fp.entryName = "TestPkg:Plain"
        fp.layer = "F.Cu"
        fp.position = Position(X=50, Y=50, angle=0)
        fp.reference = Property(key="Reference", value="R1")
        fp.value = Property(key="Value", value="10k")
        fp.zones = []
        board.footprints = [fp]
        pcb_path = str(tmp_path / "no_keepout.kicad_pcb")
        board.filePath = pcb_path
        board.to_file()
        out_path = str(tmp_path / "out.kicad_pcb")

        count = _promote_footprint_keepouts(pcb_path, out_path)

        assert count == 0
        assert not Path(out_path).exists()

    def test_rotated_footprint(self, tmp_path):
        """FP at 90 degrees: the zone KiCad stores is already turned, and is
        promoted as stored.

        KiCad's RotatePoint, which put the zone where the file holds it, is
        ``bx = fp_x + lx*cos(a) + ly*sin(a)`` and
        ``by = fp_y - lx*sin(a) + ly*cos(a)``. The signs are the transpose of
        the textbook matrix because the stored angle is counter-clockwise as
        seen on screen while file Y points down. So the square's corner at
        (10, 0) in the footprint's frame is stored, and promoted, at (100, 90),
        not (100, 110).
        """
        pcb_path = _make_board_with_fp_keepout(tmp_path, fp_angle=90, fp_x=100, fp_y=100)
        out_path = str(tmp_path / "out.kicad_pcb")

        count = _promote_footprint_keepouts(pcb_path, out_path)

        assert count == 1
        out_board = Board.from_file(out_path)
        coords = out_board.zones[0].polygons[0].coordinates
        xs = [round(c.X, 3) for c in coords]
        ys = [round(c.Y, 3) for c in coords]
        # (0,0)->100,100  (10,0)->100,90  (10,10)->110,90  (0,10)->110,100
        assert xs[0] == pytest.approx(100.0, abs=0.01)
        assert ys[0] == pytest.approx(100.0, abs=0.01)
        assert xs[1] == pytest.approx(100.0, abs=0.01)
        assert ys[1] == pytest.approx(90.0, abs=0.01)
        assert xs[2] == pytest.approx(110.0, abs=0.01)
        assert ys[2] == pytest.approx(90.0, abs=0.01)
        _assert_promoted_as_stored(pcb_path, out_path)

    def test_multiple_polygons(self, tmp_path):
        """A zone's later polygons are holes in its first, so it stays one zone.

        KiCad's zone parser makes the first polygon the outline and every later
        one a hole in it. Promoted one polygon per zone, the hole came out as a
        keepout of its own, covering the very area the cutout exists to free.
        Off the origin, so a hole moved by the footprint's position would show.
        """
        pcb_path = _make_board_with_fp_keepout(
            tmp_path, fp_x=100, fp_y=50, holes=[[(2, 2), (4, 2), (4, 4), (2, 4)]]
        )
        out_path = str(tmp_path / "out.kicad_pcb")

        count = _promote_footprint_keepouts(pcb_path, out_path)

        assert count == 1
        out_board = Board.from_file(out_path)
        assert len(out_board.zones) == 1
        outline, hole = out_board.zones[0].polygons
        assert [(round(c.X, 3), round(c.Y, 3)) for c in outline.coordinates] == [
            (100, 50),
            (110, 50),
            (110, 60),
            (100, 60),
        ]
        assert [(round(c.X, 3), round(c.Y, 3)) for c in hole.coordinates] == [
            (102, 52),
            (104, 52),
            (104, 54),
            (102, 54),
        ]
        _assert_promoted_as_stored(pcb_path, out_path)

    def test_back_side_footprint_keepout(self, tmp_path):
        """FP on B.Cu: its zone is promoted as stored, with no mirroring.

        KiCad's flip mirrors a footprint's zone in board coordinates when it
        flips the footprint, so what the file holds for a B.Cu footprint is
        already where the zone lies. Mirroring it again here moved every
        back-side keepout by twice its offset.
        """
        pcb_path = _make_board_with_fp_keepout(
            tmp_path, fp_angle=0, fp_layer="B.Cu", fp_x=100, fp_y=100
        )
        out_path = str(tmp_path / "out.kicad_pcb")

        count = _promote_footprint_keepouts(pcb_path, out_path)

        assert count == 1
        out_board = Board.from_file(out_path)
        coords = out_board.zones[0].polygons[0].coordinates
        xs = [round(c.X, 3) for c in coords]
        ys = [round(c.Y, 3) for c in coords]
        # (0,0)->100,100  (10,0)->110,100  (10,10)->110,110  (0,10)->100,110
        assert xs[0] == pytest.approx(100.0, abs=0.01)
        assert ys[0] == pytest.approx(100.0, abs=0.01)
        assert xs[1] == pytest.approx(110.0, abs=0.01)
        assert ys[1] == pytest.approx(100.0, abs=0.01)
        _assert_promoted_as_stored(pcb_path, out_path)

    def test_fp_without_position_is_promoted(self, tmp_path):
        """An FP written without (at) sits at the origin, unturned, and its
        zone's points are board coordinates whatever the footprint's
        position, so it is promoted like any other, its points as stored."""
        board = Board.create_new()
        board.nets = [Net(number=0, name="")]

        fp = Footprint()
        fp.entryName = "TestPkg:NoPos"
        fp.layer = "F.Cu"
        fp.position = None
        fp.reference = Property(key="Reference", value="U3")
        fp.value = Property(key="Value", value="NOPOS")

        keepout_zone = Zone()
        keepout_zone.net = 0
        keepout_zone.netName = ""
        keepout_zone.layers = ["F.Cu"]
        keepout_zone.hatch = Hatch(style="edge", pitch=0.5)
        keepout_zone.keepoutSettings = KeepoutSettings(
            tracks="not_allowed",
            vias="not_allowed",
            pads="not_allowed",
            copperpour="not_allowed",
            footprints="not_allowed",
        )
        poly = ZonePolygon()
        poly.coordinates = [Position(X=0, Y=0), Position(X=5, Y=0), Position(X=5, Y=5)]
        keepout_zone.polygons = [poly]
        fp.zones = [keepout_zone]
        board.footprints = [fp]
        pcb_path = str(tmp_path / "nopos.kicad_pcb")
        board.filePath = pcb_path
        board.to_file()
        out_path = str(tmp_path / "out.kicad_pcb")

        count = _promote_footprint_keepouts(pcb_path, out_path)

        assert count == 1
        assert Path(out_path).exists()
        _assert_promoted_as_stored(pcb_path, out_path)

    def test_source_zone_not_mutated(self, tmp_path):
        """The zone copy is deep, the CST twin of the old deep_copy_isolation.

        Promoting the zone must leave the footprint's own zone on the points
        it holds, and the source file must not be written at all.
        """
        pcb_path = _make_board_with_fp_keepout(tmp_path, fp_x=100, fp_y=100)
        out_path = str(tmp_path / "out.kicad_pcb")
        before = Path(pcb_path).read_bytes()

        _promote_footprint_keepouts(pcb_path, out_path)

        assert Path(pcb_path).read_bytes() == before
        out_root = _cst.parse(Path(out_path).read_bytes()).lists[0]
        fp_zone = out_root.find("footprint").find("zone")
        assert [_xy(p) for p in fp_zone.find("polygon").find("pts").find_all("xy")] == [
            (100.0, 100.0),
            (110.0, 100.0),
            (110.0, 110.0),
            (100.0, 110.0),
        ]
        # The restrictions travel with the copy rather than being aliased away.
        assert _keepout_dict(out_root.find("zone").find("keepout")) == _keepout_dict(
            fp_zone.find("keepout")
        )

    def test_dsn_source_branching_with_keepouts(self, tmp_path):
        """count > 0 means out_path is written."""
        pcb_path = _make_board_with_fp_keepout(tmp_path)
        out_path = str(tmp_path / "out.kicad_pcb")

        count = _promote_footprint_keepouts(pcb_path, out_path)

        assert count > 0
        assert Path(out_path).exists()

    def test_dsn_source_branching_without_keepouts(self, tmp_path):
        """count == 0 means out_path is NOT written."""
        board = Board.create_new()
        board.nets = [Net(number=0, name="")]
        pcb_path = str(tmp_path / "empty.kicad_pcb")
        board.filePath = pcb_path
        board.to_file()
        out_path = str(tmp_path / "out.kicad_pcb")

        count = _promote_footprint_keepouts(pcb_path, out_path)

        assert count == 0
        assert not Path(out_path).exists()

    def test_save_failure_raises_tool_error(self, tmp_path):
        """An unwritable output path surfaces as ToolError, not a raw OSError."""
        pcb_path = _make_board_with_fp_keepout(tmp_path)
        out_path = str(tmp_path / "no_such_dir" / "out.kicad_pcb")

        with pytest.raises(ToolError, match="Failed to prepare PCB for autorouting"):
            _promote_footprint_keepouts(pcb_path, out_path)

    @requires_cli
    @pytest.mark.parametrize(
        "holes", [(), [[(2, 2), (4, 2), (4, 4), (2, 4)]]], ids=["plain", "cutout"]
    )
    def test_kicad_accepts_the_promoted_board(self, tmp_path, holes):
        """The live oracle for the promoted zone: KiCad itself loads the file
        and runs DRC on it, on whichever major the runner has installed. The
        promoted board's only real consumer is pcbnew, so our own parser
        reading it back proves nothing. The cutout case reaches KiCad as one
        zone carrying both polygons."""
        pcb_path = _make_board_with_fp_keepout(
            tmp_path, fp_angle=45, fp_x=100, fp_y=100, holes=holes
        )
        out_path = str(tmp_path / "out.kicad_pcb")

        assert _promote_footprint_keepouts(pcb_path, out_path) == 1
        zone = _cst.parse(Path(out_path).read_bytes()).lists[0].find("zone")
        assert len(zone.find_all("polygon")) == 1 + len(holes)

        result = run_drc(pcb_path=out_path, output_dir=str(tmp_path))
        assert result.violation_count >= 0

    def test_promoted_keepouts_keep_their_board_coordinates(self, tmp_path):
        """N10. A KiCad-form board, a footprint at (100, 50) turned 30 degrees whose
        keepout, with a hole, is stored in board coordinates: pcbnew reads the
        promoted board-level zone with exactly the outline and hole it reads for the
        footprint's own zone. The tool turned and moved the stored points again."""
        reason = PJ.skip_reason()
        if reason:
            pytest.skip(reason)
        pcb_path = _make_board_with_fp_keepout(
            tmp_path, fp_angle=30, fp_x=100, fp_y=50, holes=[[(2, 2), (4, 2), (4, 4), (2, 4)]]
        )
        out_path = str(tmp_path / "out.kicad_pcb")
        assert _promote_footprint_keepouts(pcb_path, out_path) == 1
        source = PJ.run(_ZONE_OUTLINES, {"board": pcb_path})
        promoted = PJ.run(_ZONE_OUTLINES, {"board": out_path})
        if len(source["footprints"]) != 1 or source["board"]:
            raise RuntimeError(f"pcbnew reads the source board's zones as {source}")
        assert promoted["board"] == source["footprints"]

    def test_net_tokens_k9_numeric(self, tmp_path):
        """A KiCad 9 format board gets the numeric (net 0) plus empty net_name."""
        pcb_path = _make_board_with_fp_keepout(tmp_path)
        out_path = str(tmp_path / "out.kicad_pcb")

        assert _promote_footprint_keepouts(pcb_path, out_path) == 1

        zone = _cst.parse(Path(out_path).read_bytes()).lists[0].find("zone")
        assert zone.find("net").atoms[1].text == "0"
        assert zone.find("net_name").atoms[1].text == ""

    def test_net_tokens_absent_on_kicad10(self, tmp_path):
        """A KiCad 10 format board gets no net tokens at all on a rule area:
        numeric references are silently rebound there (ADR-2 guardrail 5)."""
        from test_cst_pcb import _bump_version

        pcb_path = _bump_version(_make_board_with_fp_keepout(tmp_path))
        out_path = str(tmp_path / "out.kicad_pcb")

        assert _promote_footprint_keepouts(pcb_path, out_path) == 1

        zone = _cst.parse(Path(out_path).read_bytes()).lists[0].find("zone")
        assert zone.find("net") is None
        assert zone.find("net_name") is None
        assert zone.find("keepout") is not None


#: Each zone pcbnew reads, at board level and in footprints: every outline and hole of
#: it as its points in nanometres.
_ZONE_OUTLINES = r"""
import pcbnew


def chains(zone):
    poly = zone.Outline()
    out = []
    for i in range(poly.OutlineCount()):
        rings = [poly.COutline(i)] + [poly.CHole(i, h) for h in range(poly.HoleCount(i))]
        for ring in rings:
            out.append([[ring.CPoint(k).x, ring.CPoint(k).y] for k in range(ring.PointCount())])
    return out


board = pcbnew.LoadBoard(payload["board"])
result = {
    "board": [chains(z) for z in board.Zones()],
    "footprints": [chains(z) for fp in board.GetFootprints() for z in fp.Zones()],
}
"""


class TestJarJavaRequirement:
    """The Java floor has to come from the jar, because FREEROUTING_JAR can
    name any build.

    The download floated too until it was pinned, fetching whatever release was
    newest with no record of what it got. Measured 2026-08-14 on the cached
    freerouting.jar (build 2026-08-07): its class files are major 69, which is
    Java 25, against a check that accepted anything from 17 up. Every machine
    between the two passed the preflight and then died inside the router, with
    the preflight's blessing.
    """

    @staticmethod
    def _jar(tmp_path, major: int, *, mr_major: int | None = None):
        """A jar carrying one class file at *major*, optionally with a
        higher-versioned multi-release copy that must be ignored."""
        import struct
        import zipfile

        def klass(m: int) -> bytes:
            return b"\xca\xfe\xba\xbe" + struct.pack(">HH", 0, m) + b"\x00" * 8

        p = tmp_path / "fake.jar"
        with zipfile.ZipFile(p, "w") as zf:
            zf.writestr("app/Main.class", klass(major))
            if mr_major is not None:
                zf.writestr(f"META-INF/versions/{mr_major - 44}/app/Main.class", klass(mr_major))
        return str(p)

    def test_reads_the_major_from_the_class_files(self, tmp_path):
        assert jar_java_requirement(self._jar(tmp_path, 69)) == 25
        assert jar_java_requirement(self._jar(tmp_path, 61)) == 17

    def test_a_multi_release_copy_does_not_raise_the_floor(self, tmp_path):
        """Requiring the highest would refuse a Java the jar runs on fine."""
        assert jar_java_requirement(self._jar(tmp_path, 61, mr_major=69)) == 17

    def test_an_unreadable_jar_is_unknown_not_fatal(self, tmp_path):
        bad = tmp_path / "not.jar"
        bad.write_bytes(b"this is not a zip")
        assert jar_java_requirement(str(bad)) is None
        assert jar_java_requirement(str(tmp_path / "absent.jar")) is None

    def test_the_pinned_jar_needs_the_floor(self):
        """Not a fixture: the pinned jar itself, when this machine has it cached
        or FREEROUTING_JAR names a copy. Its class files judge _JAVA_FLOOR, which
        is what the docs state."""
        jar = find_jar()
        if jar is None or not _fr_module._is_pinned(Path(jar)):
            pytest.skip("the pinned freerouting jar is not on this machine")
        assert jar_java_requirement(jar) == _fr_module._JAVA_FLOOR


class TestCheckJavaAgainstTheJar:
    def _java(self, version: str):
        return patch(
            "subprocess.run",
            return_value=subprocess.CompletedProcess(
                args=[], returncode=0, stdout="", stderr=f'openjdk version "{version}" 2026-01-01\n'
            ),
        )

    def test_java17_is_refused_for_a_java25_jar(self, tmp_path):
        jar = TestJarJavaRequirement._jar(tmp_path, 69)
        with self._java("17.0.9"):
            msg = check_java(jar, java="/fake/java")
        assert msg and "17" in msg and "25" in msg
        # The remedy must not be Debian-only; this server ships on three OSes.
        assert "adoptium" in msg

    def test_java25_passes_the_same_jar(self, tmp_path):
        jar = TestJarJavaRequirement._jar(tmp_path, 69)
        with self._java("25.0.1"):
            assert check_java(jar, java="/fake/java") is None

    def test_no_jar_falls_back_to_the_floor(self):
        """The floor is the pinned jar's own requirement, Java 25."""
        with self._java("25.0.1"):
            assert check_java(java="/fake/java") is None
        with self._java("24.0.1"):
            assert check_java(java="/fake/java") is not None

    def test_a_wedged_java_is_reported_not_raised(self):
        """TimeoutExpired was uncaught, so it propagated as a raw exception."""
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("java", 10)):
            msg = check_java(java="/fake/java")
        assert msg and "did not answer" in msg


def test_every_stated_java_requirement_is_the_floor():
    """autoroute_pcb's description and the pcb-layout skill said Java 17+ while
    the jar the tool fetched needed 25: class file major 69 in each of 2.2.0,
    2.2.4, 2.3.0, 2.4.1 and 2.5.0 (measured 2026-10-19). The docs state
    _JAVA_FLOOR now, and test_the_pinned_jar_needs_the_floor holds that to the
    jar itself."""
    root = Path(__file__).resolve().parent.parent
    texts = {"autoroute_pcb": autoroute_pcb.__doc__ or ""}
    docs = [root / "README.md", root / "CLAUDE.md", *sorted((root / "skills").glob("*/SKILL.md"))]
    for doc in docs:
        texts[doc.relative_to(root).as_posix()] = doc.read_text(encoding="utf-8")
    stated = sorted(
        (where, int(n)) for where, text in texts.items() for n in re.findall(r"\bJava (\d+)", text)
    )
    assert stated, "nothing states the Java requirement any more"
    assert {n for _, n in stated} == {_fr_module._JAVA_FLOOR}, stated
