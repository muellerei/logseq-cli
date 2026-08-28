# Contributing to logseq-cli

Thanks for your interest in contributing to logseq-cli!

## Development Setup

```bash
# Clone the repo
git clone https://github.com/YOUR_USERNAME/logseq-cli.git
cd logseq-cli

# Install in editable mode
pip install -e .

# Verify installation
logseq-cli --version
```

Requires Python 3.10+ and a running Logseq Desktop app with the HTTP API enabled (Settings → Advanced → Developer mode → API server).

## Project Structure

```
logseq-cli/
├── logseq_cli/
│   ├── api.py       # HTTP API client (thin wrapper around Logseq's API)
│   ├── helpers.py   # Date parsing, block processing, content formatting
│   └── cli.py       # Click CLI with all commands
├── tests/           # pytest suite (no fixtures beyond tests/conftest.py)
├── examples/        # Shell scripts for common workflows
├── AGENTS.md        # AI agent reference
├── CLAUDE.md        # Claude Code instructions
└── pyproject.toml   # Package config
```

## Making Changes

1. **Read the code first.** `cli.py` is the main file (~3800 lines). Each command is a self-contained function decorated with `@cli.command()`.

2. **Follow existing patterns.** New commands should:
   - Use `@click.option("--page", "--name", ...)` for page parameters (dual alias)
   - Include `--json` output support via the `output()` helper
   - Use `@handle_connection_error` decorator
   - Support `--dry-run` for write operations

3. **Run the test suite, and add to it.**
   ```bash
   python3 -m pytest -q
   ```
   Tests mock the API (`unittest.mock` + `CliRunner`); `tests/conftest.py` has a
   `FakeGraph` for the write paths, needed wherever a command verifies its write
   by reading back. A new command or flag ships with tests: the failure modes
   that matter here are silent ones, since Logseq answers a failed write with
   HTTP 200 + `null` rather than an error.

   Then check it against a running Logseq instance as well, because the mocks
   encode what we believe the API does:
   ```bash
   export LOGSEQ_TOKEN="your-token"
   logseq-cli your-new-command --help
   logseq-cli your-new-command --dry-run ...
   logseq-cli your-new-command ...
   ```

4. **Update documentation.** If you add or change a command:
   - Update `README.md` (command tables and usage examples)
   - Update `AGENTS.md` (if it affects common workflows)
   - Update `CLAUDE.md` (if it changes critical rules or decision trees)
   - Add an entry under `## [Unreleased]` in `CHANGELOG.md`

## Design Principles

- **Named parameters only.** No positional arguments. Every parameter uses `--flag value`.
- **Locale-independent.** Weekday/month names are always English, regardless of system locale.
- **Env var fallbacks.** User-configurable defaults via environment variables, not hardcoded values.
- **Graceful degradation.** Connection errors print a clear message and exit with code 1.
- **German + English.** `smart-query` keywords support both languages.

## Reporting Issues

Open an issue on GitHub with:
- What you tried (command + arguments)
- What happened (error message or unexpected output)
- What you expected
- Your Logseq version and OS

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
