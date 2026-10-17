"""Say whether a pull request touches only files a workflow may skip.

Usage: git diff --name-only HEAD^1 HEAD | python docs_only.py SKIP_LIST_FILE

The skip list holds one pattern per line: an exact path, or a directory
prefix ending in "/**". Anything else (a "*" elsewhere, "?", "[") is refused,
because GitHub's glob rules differ from Python's and a pattern misread here
would skip a check the change needed. Prints "code=false" when every changed
file matches a pattern and "code=true" otherwise, including when nothing
changed, so the caller can append the line to $GITHUB_OUTPUT.
"""

from __future__ import annotations

import sys
from pathlib import Path


def load_patterns(path: Path) -> list[str]:
    patterns = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        body = line[:-3] if line.endswith("/**") else line
        if any(c in body for c in "*?["):
            raise SystemExit(f"unsupported pattern {line!r}: use an exact path or a dir/** prefix")
        patterns.append(line)
    return patterns


def skippable(name: str, patterns: list[str]) -> bool:
    for p in patterns:
        if p.endswith("/**"):
            if name.startswith(p[:-2]):
                return True
        elif name == p:
            return True
    return False


def docs_only(changed: list[str], patterns: list[str]) -> bool:
    """True when something changed and every changed file may be skipped."""
    return bool(changed) and all(skippable(n, patterns) for n in changed)


def main() -> None:
    patterns = load_patterns(Path(sys.argv[1]))
    changed = [line.strip() for line in sys.stdin if line.strip()]
    print(f"code={'false' if docs_only(changed, patterns) else 'true'}")


if __name__ == "__main__":
    main()
