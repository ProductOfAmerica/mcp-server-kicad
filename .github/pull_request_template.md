## Summary

<!-- What changes and why. -->

## Test Plan

<!-- The commands you ran and what they printed. -->

- [ ] Targeted tests pass (`uv run pytest <the test files this change touches> -v`)
- [ ] Full suite passes (`uv run pytest -v -n auto`)
- [ ] Lints pass (`uv run ruff check . && uv run ruff format --check .`)
- [ ] Types check (`uv run pyright`)

## Risk

<!-- What could break, for whom, and which check would catch it. -->

## How to revert

<!-- Usually `git revert -m 1 <merge commit>`. Say so if that is not enough, for example after a release, or when the change rewrites files or settings outside the repository. -->
