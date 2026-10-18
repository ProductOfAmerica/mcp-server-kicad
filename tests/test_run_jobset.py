"""run_jobset gives kicad-cli the jobset the way kicad-cli takes it, and says what ran.

``kicad-cli jobset run`` takes the jobset by its --file option and the project as its
positional input. The tool used to pass the jobset as the input, which kicad-cli reads as a
project it cannot find a job in: it ran nothing, exited 0, and the tool reported success.

Judges, none of them the code under test: the files KiCad's own jobs write, and the lines
kicad-cli itself prints, which these tests read from a run of kicad-cli on the same jobset.
"""

from __future__ import annotations

import json
import re
import subprocess
import uuid
from pathlib import Path

import pytest
from conftest import requires_cli
from mcp.server.mcpserver.exceptions import ToolError

from mcp_server_kicad import project
from mcp_server_kicad._shared import _find_kicad_cli

_ANSI = re.compile(r"\x1b\[[0-9;]*m")
#: One line per output kicad-cli runs, spelled by a wxT literal that no locale translates.
_SUMMARY = re.compile(r"\d+ jobs succeeded, \d+ job failed")


def _erc_job() -> dict:
    """A job KiCad runs on the project's root schematic: the ERC report as JSON."""
    return {
        "id": str(uuid.uuid4()),
        "type": "sch_erc",
        "description": "",
        "settings": {
            "format": "json",
            "units": "mm",
            "severity": 15,
            "fail_on_error": False,
            "output_filename": "erc.json",
        },
    }


def _copy_job(source: str) -> dict:
    """A job that fails when its source is not there."""
    return {
        "id": str(uuid.uuid4()),
        "type": "special_copyfiles",
        "description": "",
        "settings": {"source": source, "dest": "", "zero_copies_error": False, "overwrite": True},
    }


def _folder(path: str) -> dict:
    """An output that copies what its jobs wrote into a folder."""
    return {
        "id": str(uuid.uuid4()),
        "type": "folder",
        "only": [],
        "description": "",
        "settings": {"output_path": path},
    }


def _jobset(path: Path, jobs: list, outputs: list) -> Path:
    path.write_text(
        json.dumps({"jobs": jobs, "meta": {"version": 1}, "outputs": outputs}, indent=2),
        encoding="utf-8",
    )
    return path


def _project(tmp_path: Path, name: str = "jp") -> Path:
    """A project folder with a root schematic, and the .kicad_pro that makes it a project."""
    directory = tmp_path / "proj"
    project.create_project(directory=str(directory), name=name)
    return directory


def _kicad_clis_own_run(jobset: Path) -> tuple[int, list[str]]:
    """Exit code and summary lines of ``kicad-cli jobset run --file J P`` on the jobset,
    run from the project folder as kicad-cli takes it. A premise for the tests that
    compare with it: RuntimeError when kicad-cli cannot be run at all."""
    cli = _find_kicad_cli()
    pro = next(jobset.parent.glob("*.kicad_pro"))
    if cli is None:
        raise RuntimeError("no kicad-cli")
    run = subprocess.run(
        [cli, "jobset", "run", "--file", str(jobset), str(pro)],
        capture_output=True,
        text=True,
        errors="replace",
        cwd=jobset.parent,
    )
    return run.returncode, _SUMMARY.findall(_ANSI.sub("", run.stdout))


@requires_cli
def test_the_jobs_of_the_jobset_run(tmp_path):
    """The jobset reaches kicad-cli by --file with the project as its input, so the ERC
    job runs on the project's root schematic and the folder output receives its report.
    Named after its project, as the project manager names one, the jobset used to be read
    as the project: kicad-cli exited 0 having run nothing, and the tool said it succeeded."""
    directory = _project(tmp_path)
    reports = tmp_path / "reports"
    jobset = _jobset(directory / "jp.kicad_jobset", [_erc_job()], [_folder(str(reports))])

    result = project.run_jobset(str(jobset))

    report = json.loads((reports / "erc.json").read_text(encoding="utf-8"))
    assert report["source"].endswith("jp.kicad_sch"), report["source"]
    assert "1 jobs succeeded, 0 job failed" in result


@requires_cli
def test_a_relative_output_folder_lands_in_the_project(tmp_path, monkeypatch):
    """kicad-cli resolves a relative output folder against its working directory, so the
    tool runs it in the project's folder, whatever folder the server was started in."""
    directory = _project(tmp_path)
    server_cwd = tmp_path / "server-cwd"
    server_cwd.mkdir()
    jobset = _jobset(directory / "jp.kicad_jobset", [_erc_job()], [_folder("fab")])
    monkeypatch.chdir(server_cwd)

    project.run_jobset(str(jobset))

    assert (directory / "fab" / "erc.json").is_file()
    assert list(server_cwd.iterdir()) == []


@requires_cli
def test_a_relative_jobset_path_still_finds_its_jobset(tmp_path, monkeypatch):
    directory = _project(tmp_path)
    _jobset(directory / "jp.kicad_jobset", [_erc_job()], [_folder("out")])
    monkeypatch.chdir(tmp_path)

    project.run_jobset(str(Path("proj") / "jp.kicad_jobset"))

    assert (directory / "out" / "erc.json").is_file()


_NOT_A_JOBSET = b"this is not json {"


@requires_cli
@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("no-outputs", {"jobs": [_erc_job()], "outputs": []}),
        ("no-jobs", {"jobs": [], "outputs": [_folder("unused")]}),
        ("not-json", _NOT_A_JOBSET),
    ],
)
def test_a_jobset_that_runs_nothing_is_an_error(tmp_path, name, content):
    """kicad-cli exits 0 for each of these and runs no job; reporting success for them was
    the tool's other half of the defect. The error carries the counts kicad-cli printed,
    or says it printed none."""
    directory = _project(tmp_path)
    jobset = directory / f"{name}.kicad_jobset"
    if isinstance(content, bytes):
        jobset.write_bytes(content)
    else:
        _jobset(jobset, content["jobs"], content["outputs"])
    code, summaries = _kicad_clis_own_run(jobset)
    if code != 0 or any(not s.startswith("0 jobs succeeded, 0 job failed") for s in summaries):
        raise RuntimeError(f"premise: kicad-cli ran something for {name}: {code}, {summaries}")

    with pytest.raises(ToolError) as refused:
        project.run_jobset(str(jobset))

    message = str(refused.value)
    assert jobset.name in message
    assert "ran nothing" in message
    assert (summaries[0] if summaries else "no job counts") in message


@requires_cli
def test_a_failed_job_is_an_error_with_kicad_clis_counts(tmp_path):
    directory = _project(tmp_path)
    jobset = _jobset(
        directory / "copy.kicad_jobset",
        [_copy_job("no/such/file.txt")],
        [_folder(str(tmp_path / "out"))],
    )
    code, summaries = _kicad_clis_own_run(jobset)
    if code == 0 or summaries != ["0 jobs succeeded, 1 job failed"]:
        raise RuntimeError(f"premise: kicad-cli did not fail the job: {code}, {summaries}")

    with pytest.raises(ToolError) as refused:
        project.run_jobset(str(jobset))

    message = str(refused.value)
    assert jobset.name in message
    assert f"exit code {code}" in message
    assert summaries[0] in message


@requires_cli
def test_an_output_that_cannot_run_is_an_error(tmp_path):
    """An output with no folder fails before any job runs: kicad-cli exits non-zero and
    prints no counts."""
    directory = _project(tmp_path)
    jobset = _jobset(directory / "blank.kicad_jobset", [_erc_job()], [_folder("")])
    code, summaries = _kicad_clis_own_run(jobset)
    if code == 0 or summaries:
        raise RuntimeError(f"premise: kicad-cli ran the blank output: {code}, {summaries}")

    with pytest.raises(ToolError) as refused:
        project.run_jobset(str(jobset))

    assert f"exit code {code}" in str(refused.value)


def _spawn_is_a_failure(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("a process was started")

    monkeypatch.setattr(subprocess, "run", boom)


def test_a_jobset_without_a_project_beside_it_is_refused(tmp_path, monkeypatch):
    """The project is the one .kicad_pro in the jobset's own folder; one in the folder
    above does not count. Refused before any process starts."""
    _project(tmp_path)
    inner = tmp_path / "proj" / "jobs"
    inner.mkdir()
    jobset = _jobset(inner / "check.kicad_jobset", [_erc_job()], [_folder("out")])
    _spawn_is_a_failure(monkeypatch)

    with pytest.raises(ToolError, match=r"No \.kicad_pro beside check\.kicad_jobset"):
        project.run_jobset(str(jobset))


def test_a_jobset_with_several_projects_beside_it_is_refused(tmp_path, monkeypatch):
    directory = _project(tmp_path)
    (directory / "other.kicad_pro").write_text("{}", encoding="utf-8")
    jobset = _jobset(directory / "check.kicad_jobset", [_erc_job()], [_folder("out")])
    _spawn_is_a_failure(monkeypatch)

    with pytest.raises(ToolError) as refused:
        project.run_jobset(str(jobset))

    assert "jp.kicad_pro" in str(refused.value)
    assert "other.kicad_pro" in str(refused.value)
