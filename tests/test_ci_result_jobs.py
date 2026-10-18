"""main's ruleset requires one check from each of ci.yml and kicad-suite.yml.

That check is the workflow's `result` job, so it is only as good as its
`needs:` list: a job left off the list can fail while the required check
passes. A `paths-ignore` filter would break it the other way, leaving a
docs-only pull request waiting on a check that never reports. The workflows
are read as text (PyYAML is not a dependency), as test_packaging_metadata.py
reads ci.yml.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
GATED = ["ci.yml", "kicad-suite.yml"]

_DOCS_ONLY = REPO_ROOT / ".github" / "tools" / "docs_only.py"
_spec = importlib.util.spec_from_file_location("docs_only", _DOCS_ONLY)
assert _spec and _spec.loader
docs_only = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(docs_only)


def _jobs(text: str) -> dict[str, str]:
    """Each top-level job's name and its text, up to the next job."""
    body = text.split("\njobs:\n", 1)[1]
    parts = re.split(r"^  ([A-Za-z0-9_-]+):\n", body, flags=re.MULTILINE)
    return dict(zip(parts[1::2], parts[2::2], strict=True))


@pytest.mark.parametrize("name", GATED)
def test_result_job_needs_every_other_job(name):
    jobs = _jobs((WORKFLOWS / name).read_text(encoding="utf-8"))
    result = jobs["result"]
    always = re.search(r"^    if: always\(\)$", result, re.MULTILINE)
    assert always, "result must run when a job it needs fails"
    needs = re.search(r"^    needs: \[([^\]]*)\]$", result, re.MULTILINE)
    assert needs, "result has no one-line needs: list"
    assert {n.strip() for n in needs.group(1).split(",")} == set(jobs) - {"result"}


@pytest.mark.parametrize("name", GATED)
def test_no_paths_filter(name):
    text = (WORKFLOWS / name).read_text(encoding="utf-8")
    assert not re.search(r"^\s+paths(-ignore)?:", text, re.MULTILINE)


@pytest.mark.parametrize("name", GATED)
def test_skip_list_patterns_are_supported(name, tmp_path):
    text = (WORKFLOWS / name).read_text(encoding="utf-8")
    block = re.search(r"<<'EOF'\n(.*?)^\s+EOF$", text, re.MULTILINE | re.DOTALL)
    assert block, f"{name} has no skip list"
    skip = tmp_path / "skip.txt"
    skip.write_text(block.group(1), encoding="utf-8")
    assert docs_only.load_patterns(skip)


@pytest.mark.parametrize(
    ("changed", "skipped"),
    [
        (["README.md", "docs/adr.md", "NOTICE"], True),
        (["README.md", "mcp_server_kicad/pcb.py"], False),
        ([], False),
        (["docs2/notes.md"], False),
        (["docs"], False),
        (["sub/README.md"], False),
    ],
)
def test_docs_only(changed, skipped):
    assert docs_only.docs_only(changed, ["README.md", "NOTICE", "docs/**"]) is skipped


@pytest.mark.parametrize("pattern", ["docs/*.md", "**.md", "file?.txt", "[ab].md"])
def test_unsupported_pattern_refused(pattern, tmp_path):
    skip = tmp_path / "skip.txt"
    skip.write_text(pattern + "\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        docs_only.load_patterns(skip)
