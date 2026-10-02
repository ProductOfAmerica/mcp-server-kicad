# Contributing

Thank you for your interest in contributing to mcp-server-kicad!

## Development Setup

1. Fork and clone the repository:

   ```bash
   git clone https://github.com/<your-username>/mcp-server-kicad.git
   cd mcp-server-kicad
   ```

2. Install [uv](https://docs.astral.sh/uv/), then the locked environment with the dev extra, which is exactly what CI installs:

   ```bash
   uv sync --frozen --all-extras --dev
   ```

3. Run tests:

   ```bash
   uv run pytest -v -n auto
   ```

4. Run lints and the type check:

   ```bash
   uv run ruff check .
   uv run ruff format --check .
   uv run pyright
   ```

To run the same ruff on every commit, install [pre-commit](https://pre-commit.com/) 4.4 or newer and run `pre-commit install`. Its hooks call `uv run`, so they use the ruff version `uv.lock` pins.

## Workflow

1. Create a branch from `main` for your change.
2. Make your changes.
3. Add or update tests as needed.
4. Ensure tests and lints pass locally.
5. Open a pull request against `main`.

## Tests That Need KiCad

Tests that shell out to `kicad-cli` (ERC, DRC, exports) need KiCad installed, and so do the few that drive pcbnew's Python bindings. `kicad-cli` is found through `KICAD_CLI_PATH`, then your `PATH`, then `/Applications/KiCad/KiCad.app` on macOS, then the versioned install folders under `Program Files` and `AppData` on Windows. Without it those tests skip, and so does the autouse fixture that checks every generated schematic and board loads in `kicad-cli`, so a green run proves much less. Confirm it resolves before trusting one.
