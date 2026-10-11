"""Packaging metadata that has to agree with what CI actually tests.

PyPI shows the trove classifiers as the Python versions the package supports.
The claim is only true if each one is a leg of ci.yml's test job, and every leg
is a version users can rely on, so the two lists must be equal. Before this
test the classifiers named 3.10 alone while the matrix ran 3.10 through 3.14.

pyproject.toml is read as TOML. ci.yml is read as text (PyYAML is not a
dependency), so the test confines itself to the test job and refuses the
matrix features it does not model rather than misreading them.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # pytest itself needs tomli on 3.10, so the dev environment always has it
    import tomli as tomllib

REPO_ROOT = Path(__file__).resolve().parent.parent
CLASSIFIER = "Programming Language :: Python :: "


def _version(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.split("."))


def _test_job() -> str:
    """The text of ci.yml's `test` job, up to the next job or the end of the file."""
    text = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    match = re.search(r"^  test:\n(.*?)(?=^  \S|\Z)", text, re.MULTILINE | re.DOTALL)
    assert match, "ci.yml has no test job"
    return match.group(1)


def _ci_matrix_versions() -> list[str]:
    job = _test_job()
    for key in ("include", "exclude", "continue-on-error"):
        assert not re.search(rf"^\s+{key}:", job, re.MULTILINE), (
            f"ci.yml's test job uses {key}:, which this test does not model; extend the test first"
        )
    lists = re.findall(r"^\s+python-version:\s*\[([^\]]*)\]", job, re.MULTILINE)
    assert len(lists) == 1, f"the test job holds {len(lists)} python-version lists, not one"
    versions = re.findall(r"""["'](\d+\.\d+)["']""", lists[0])
    assert versions, "ci.yml's python-version list is empty"
    # A leg tests the version it is named for only if setup-uv gets the matrix
    # value and the guard step compares the interpreter with it: on 2026-10-01
    # every leg of this matrix ran 3.10.
    assert re.search(r"^\s+python-version: \$\{\{ matrix\.python-version \}\}$", job, re.MULTILINE)
    assert re.search(r"^\s+EXPECTED: \$\{\{ matrix\.python-version \}\}$", job, re.MULTILINE)
    return versions


def _project() -> dict:
    with open(REPO_ROOT / "pyproject.toml", "rb") as f:
        return tomllib.load(f)["project"]


def test_classifiers_match_the_ci_matrix() -> None:
    named = [c.removeprefix(CLASSIFIER) for c in _project()["classifiers"]]
    versions = [v for v in named if re.fullmatch(r"\d+\.\d+", v)]
    assert sorted(versions, key=_version) == sorted(_ci_matrix_versions(), key=_version)


def test_requires_python_floor_is_the_oldest_tested_version() -> None:
    match = re.fullmatch(r">=(\d+\.\d+)", _project()["requires-python"])
    assert match, "requires-python is not a plain >=X.Y floor"
    assert match.group(1) == min(_ci_matrix_versions(), key=_version)
