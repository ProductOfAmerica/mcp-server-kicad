# Contributing

Thank you for your interest in contributing to mcp-server-kicad!

## Development Setup

1. Fork and clone the repository:

   ```bash
   git clone https://github.com/<your-username>/mcp-server-kicad.git
   cd mcp-server-kicad
   ```

2. Create a virtual environment and install dev dependencies:

   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -e ".[dev]"
   ```

3. Run tests:

   ```bash
   pytest -v
   ```

4. Run lints:

   ```bash
   ruff check .
   ruff format --check .
   ```

## Workflow

1. Create a branch from `main` for your change.
2. Make your changes.
3. Add or update tests as needed.
4. Ensure tests and lints pass locally.
5. Open a pull request against `main`.

## Notes on Export Tests

Tests for the export server require `kicad-cli`. It is found on your `PATH`, inside `/Applications/KiCad/KiCad.app` on macOS, or in the versioned Windows install folders under `Program Files` and `AppData`; set `KICAD_CLI_PATH` if yours is elsewhere. If `kicad-cli` is not found, those tests are automatically skipped, and so is the autouse fixture that checks every generated `.kicad_sch` is parseable, so it is worth confirming it resolves. The schematic and PCB server tests do not require KiCad.
