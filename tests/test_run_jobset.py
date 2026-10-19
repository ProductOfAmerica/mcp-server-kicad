"""run_jobset gives kicad-cli the jobset the way kicad-cli takes it, and says what ran.

``kicad-cli jobset run`` takes the jobset by its --file option and the project as its
positional input. The tool used to pass the jobset as the input, so kicad-cli read it as the
project: for a jobset named after its project it ran nothing and exited 0, and the tool
reported success.

Judges, none of them the code under test: the files KiCad's own jobs write, and the lines
kicad-cli itself prints, which these tests read from a run of kicad-cli on the same jobset.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

import pytest
from conftest import requires_cli
from mcp.server.mcpserver.exceptions import ToolError

from mcp_server_kicad import project
from mcp_server_kicad._shared import _CLI_TIMEOUT, _find_kicad_cli

_ANSI = re.compile(r"\x1b\[[0-9;]*m")
#: One line per output kicad-cli runs (kicad/jobs_runner.cpp:324-328 at 9.0.8).
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


def _kicad_clis_own_run(jobset: Path) -> tuple[int, list[str], bytes | None]:
    """What ``kicad-cli jobset run --file J P`` does with the jobset: the exit code, the
    summary lines it prints, and the bytes the jobset holds afterwards (None when it is gone).

    Run on a copy of the jobset, of its project file and of the project's root schematic in a
    folder of its own, because a jobset kicad-cli cannot read does not survive the run, and a
    relative output folder lands there. A premise for the tests that compare with it:
    RuntimeError when kicad-cli cannot be run at all or does not finish in the time the tools
    allow it.
    """
    cli = _find_kicad_cli()
    pro = next(jobset.parent.glob("*.kicad_pro"))
    if cli is None:
        raise RuntimeError("no kicad-cli")
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as scratch:
        work = Path(scratch)
        shutil.copy2(pro, work / pro.name)
        root = pro.with_suffix(".kicad_sch")
        if root.is_file():
            shutil.copy2(root, work / root.name)
        copy = work / jobset.name
        shutil.copy2(jobset, copy)
        try:
            run = subprocess.run(
                [cli, "jobset", "run", "--file", str(copy), str(work / pro.name)],
                capture_output=True,
                text=True,
                errors="replace",
                cwd=work,
                timeout=_CLI_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"kicad-cli jobset run did not finish in {_CLI_TIMEOUT} s")
        left = copy.read_bytes() if copy.exists() else None
    return run.returncode, _SUMMARY.findall(_ANSI.sub("", run.stdout)), left


@requires_cli
def test_the_jobs_of_the_jobset_run(tmp_path):
    """The jobset reaches kicad-cli by --file with the project as its input, so the ERC
    job runs on the project's root schematic and the folder output receives its report.
    Named after its project, the jobset used to be read as the project: kicad-cli exited 0
    having run nothing, and the tool said it succeeded."""
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


@requires_cli
@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("no-outputs", {"jobs": [_erc_job()], "outputs": []}),
        ("no-jobs", {"jobs": [], "outputs": [_folder("unused")]}),
        ("not-a-jobset", {"unrelated": 1}),
    ],
)
def test_a_jobset_that_runs_nothing_is_an_error(tmp_path, name, content):
    """kicad-cli exits 0 for each of these and runs no job; reporting success for them was
    the tool's other half of the defect. The error carries the counts kicad-cli printed,
    or says it printed none."""
    directory = _project(tmp_path)
    jobset = directory / f"{name}.kicad_jobset"
    jobset.write_text(json.dumps(content), encoding="utf-8")
    code, summaries, left = _kicad_clis_own_run(jobset)
    if code != 0 or any(not s.startswith("0 jobs succeeded, 0 job failed") for s in summaries):
        raise RuntimeError(f"premise: kicad-cli ran something for {name}: {code}, {summaries}")
    written = jobset.read_bytes()

    with pytest.raises(ToolError) as refused:
        project.run_jobset(str(jobset))

    message = str(refused.value)
    assert jobset.name in message
    assert "ran nothing" in message
    assert (summaries[0] if summaries else "no job counts") in message
    assert jobset.read_bytes() == written


_BS = chr(92)  # a backslash, to write the surrogate escapes out
_EMPTY_JOBSET = json.dumps({"jobs": [], "meta": {"version": 1}, "outputs": []})


def _described(text: str) -> bytes:
    """A jobset that would run, its first job described by *text* spelled into the JSON."""
    valid = json.dumps(
        {"jobs": [_erc_job()], "meta": {"version": 1}, "outputs": [_folder("fab")]}, indent=2
    )
    return valid.replace('"description": ""', f'"description": "{text}"', 1).encode()


#: A jobset kicad-cli cannot parse: it is not JSON, it stops short, it is empty, it is saved as
#: UTF-16 (PowerShell's default), it holds a NaN, or a string in it holds a surrogate escape
#: with no partner, which Python's json reads and nlohmann's parser refuses (json.dumps writes
#: one for a byte that is not UTF-8).
_UNREADABLE = [
    pytest.param(b"this is not json {", id="not-json"),
    pytest.param(b'{"meta": {"version": 1}', id="cut-short"),
    pytest.param(b"", id="empty"),
    pytest.param(b"\xff\xfe" + _EMPTY_JOBSET.encode("utf-16-le"), id="utf-16"),
    pytest.param(b'{"jobs": [], "meta": {"version": 1, "x": NaN}, "outputs": []}', id="nan"),
    pytest.param(_described("ERC " + _BS + "udc80"), id="lone-low-surrogate"),
    pytest.param(_described("ERC " + _BS + "ud800"), id="lone-high-surrogate"),
    pytest.param(
        _described("ERC " + _BS + "ud800" + _BS + "u0041"), id="high-surrogate-then-a-letter"
    ),
]

#: The same valid jobset, commented the way nlohmann's parser takes comments (kicad-cli reads
#: the jobset with comments ignored), which Python's json refuses.
_COMMENTED = [
    pytest.param(lambda t: "// ERC for the fab house\n" + t, id="line-comment-first"),
    pytest.param(lambda t: "/* ERC for the fab house */\n" + t, id="block-comment-first"),
    pytest.param(lambda t: t + "\n// end", id="trailing-comment-without-a-newline"),
    pytest.param(lambda t: t.replace('"jobs"', '/* c */ "jobs"', 1), id="comment-between-tokens"),
    pytest.param(lambda t: chr(0xFEFF) + "// c\r" + t, id="byte-order-mark-then-a-comment"),
]


@requires_cli
def test_a_jobset_with_a_byte_order_mark_still_runs(tmp_path):
    """kicad-cli reads a UTF-8 jobset with a byte order mark."""
    directory = _project(tmp_path)
    reports = tmp_path / "reports"
    jobset = _jobset(directory / "jp.kicad_jobset", [_erc_job()], [_folder(str(reports))])
    jobset.write_bytes(b"\xef\xbb\xbf" + jobset.read_bytes())

    project.run_jobset(str(jobset))

    assert (reports / "erc.json").is_file()


@requires_cli
@pytest.mark.parametrize("comment", _COMMENTED)
def test_a_jobset_with_comments_runs_because_kicad_cli_reads_it(tmp_path, comment):
    """kicad-cli parses a jobset with // and /* */ comments ignored, runs it and leaves the
    file alone (a premise, measured here on a copy). The tool does not judge the grammar: a
    jobset it refused as 'not valid JSON' was one kicad-cli runs."""
    directory = _project(tmp_path)
    valid = _jobset(directory / "valid.kicad_jobset", [_erc_job()], [_folder("fab")])
    jobset = directory / "commented.kicad_jobset"
    jobset.write_bytes(comment(valid.read_text(encoding="utf-8")).encode("utf-8"))
    written = jobset.read_bytes()
    code, summaries, left = _kicad_clis_own_run(jobset)
    if code != 0 or summaries != ["1 jobs succeeded, 0 job failed"] or left != written:
        raise RuntimeError(f"premise: kicad-cli did not just run it: {code}, {summaries}, {left!r}")

    result = project.run_jobset(str(jobset))

    assert "1 jobs succeeded, 0 job failed" in result
    assert (directory / "fab" / "erc.json").is_file()
    assert jobset.read_bytes() == written


@requires_cli
@pytest.mark.parametrize("content", _UNREADABLE)
def test_a_jobset_kicad_cli_cannot_read_is_an_error_and_left_as_it_is(tmp_path, content):
    """kicad-cli answers a jobset it cannot parse by replacing it with an empty jobset, or by
    deleting it (a premise, measured here on a copy). The tool gives it a copy, so the jobset
    is what it was, and says that nothing ran."""
    directory = _project(tmp_path)
    jobset = directory / "broken.kicad_jobset"
    jobset.write_bytes(content)
    code, _, left = _kicad_clis_own_run(jobset)
    if left == content:
        raise RuntimeError(f"premise: kicad-cli left the unreadable jobset alone: {code}, {left!r}")

    with pytest.raises(ToolError) as refused:
        project.run_jobset(str(jobset))

    assert jobset.read_bytes() == content
    message = str(refused.value)
    assert "broken.kicad_jobset ran nothing" in message
    assert "cannot parse" in message


def test_whatever_the_process_does_to_the_file_it_is_given_the_jobset_survives(
    tmp_path, monkeypatch
):
    """The tool does not stand in for kicad-cli's reader, whose grammar is not Python's: it
    never gives the process the jobset. A process that empties the file it is given leaves
    the jobset as it was, and the copy is gone afterwards."""
    directory = _project(tmp_path)
    jobset = _jobset(directory / "jp.kicad_jobset", [_erc_job()], [_folder("out")])
    written = jobset.read_bytes()
    given: list[Path] = []

    def empties_its_file(args, check=True, cwd=None):
        path = Path(args[args.index("--file") + 1])
        given.append(path)
        assert path.read_bytes() == written  # a copy of the jobset, not some other file
        path.write_bytes(b"")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(project, "_run_cli", empties_its_file)

    with pytest.raises(ToolError, match="ran nothing"):
        project.run_jobset(str(jobset))

    assert jobset.read_bytes() == written
    assert [p.name for p in given] == ["jp.kicad_jobset"]
    assert given[0] != jobset and not given[0].exists()


@requires_cli
def test_a_failed_job_is_an_error_with_kicad_clis_counts(tmp_path):
    directory = _project(tmp_path)
    jobset = _jobset(
        directory / "copy.kicad_jobset",
        [_copy_job("no/such/file.txt")],
        [_folder(str(tmp_path / "out"))],
    )
    code, summaries, _ = _kicad_clis_own_run(jobset)
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
    code, summaries, _ = _kicad_clis_own_run(jobset)
    if code == 0 or summaries:
        raise RuntimeError(f"premise: kicad-cli ran the blank output: {code}, {summaries}")

    with pytest.raises(ToolError) as refused:
        project.run_jobset(str(jobset))

    assert f"exit code {code}" in str(refused.value)


def _spawn_is_a_failure(monkeypatch):
    """The tool starts no process. Patched where the tool looks the runner up, not in
    subprocess: the autouse oracle runs kicad-cli at teardown while this patch is still in
    place, through a runner of its own."""

    def boom(*args, **kwargs):
        raise AssertionError("a process was started")

    monkeypatch.setattr(project, "_run_cli", boom)


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
