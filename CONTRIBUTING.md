# Contributing to logseq-cli

Thanks for your interest in contributing to logseq-cli!

## Development Setup

```bash
# Clone the repo
git clone https://github.com/muellerei/logseq-cli.git
cd logseq-cli

# Install in editable mode, with the test dependencies
pip install -e ".[dev]"

# Verify installation
logseq-cli --version
```

Requires Python 3.10+ and a running Logseq Desktop app with the HTTP API enabled (Settings → Advanced → Developer mode → API server).

## Project Structure

```
logseq-cli/
├── logseq_cli/
│   ├── api.py       # HTTP API client (thin wrapper around Logseq's API)
│   ├── datalog.py   # EDN/datalog query building (value quoting, keywords)
│   ├── helpers.py   # Date parsing, block processing, content formatting
│   └── cli.py       # Click CLI with all commands
├── tests/           # pytest suite (no fixtures beyond tests/conftest.py)
├── examples/        # Shell scripts for common workflows
├── AGENTS.md        # AI agent reference
└── pyproject.toml   # Package config
```

## Making Changes

1. **Read the code first.** `cli.py` is the main file — over five thousand lines, which is more than one file should carry and is being split. Each command is a self-contained function decorated with `@cli.command()`.

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

   **Tests must be able to fail.**

   A test that confirms the fix instead of catching the bug is worth nothing
   and looks like safety. Before trusting one, remove the fix and check that
   the test goes red.

   This is not theory. A test meant to prove that `search-pages` also matches
   on `originalName` searched for `"Alpha"` — which, after `.lower()`, is
   present in `name` too. It passed, and it tested nothing; only removing the
   `originalName` branch exposed it. The fixture now uses `Q&A / Support`,
   whose ampersand does not survive into the slugged `name`.

   Tests that write to a live graph use throwaway pages with a recognisable
   prefix — `zz-probe-<timestamp>` — and delete them afterwards.

4. **A change is not done when the tests pass.** A new flag ships when it
   appears in:
   - `--help` — the option's own text, and the command epilog if the behaviour
     is not obvious from the flag name
   - the command table in `README.md`
   - `AGENTS.md`, if it affects a common workflow
   - `CHANGELOG.md` under `## [Unreleased]`

   Both flags added in 0.10.0 went out without the README row and the
   `AGENTS.md` entry, and were caught the same evening. Twenty further options
   had never been listed at all. `tests/test_readme_documents_options.py` now
   holds the command table against the registry, which covers the README row
   and nothing else on this list.

   Relative links and anchors across the Markdown files, after any of those:

   ```bash
   python3 scripts/check-links.py .
   ```

## Design Principles

- **Named parameters only.** No positional arguments. Every parameter uses `--flag value`.
- **Locale-independent.** Weekday/month names are always English, regardless of system locale.
- **Env var fallbacks.** User-configurable defaults via environment variables, not hardcoded values.
- **Graceful degradation.** Connection errors print a clear message and exit with code 1.
- **A numeric option validates its lower bound, before the first read.** Below
  zero nothing here has a meaning, and an accepted nonsense value does not fail
  loudly — it slices from the wrong end or moves a cutoff into the future and
  answers a different question than the one asked. What `0` means differs per
  option — it lifts the cap for `get-backlinks --limit` and `get-todos
  --refs-limit` and is refused everywhere else — and that belongs in the
  option's `--help` text, because that is where the caller looks. Decide it by
  running the command, not by analogy with a neighbouring option:
  `analyze-graph --days 0` reads as "today" and actually puts the cutoff at
  this moment, which can only ever match a page edited in the future.
  `tests/test_numeric_option_bounds.py` derives the list from the command
  registry, so a new option is covered the moment it exists.
- **A rejected value is reported with `fail()`, not `click.BadParameter`.**
  Every command here speaks `--json`, and `fail()` writes an error *object* on
  stderr under that flag, where Click writes a usage dump that no caller can
  parse. This applies to what a command checks itself; the shared parsers in
  `helpers.py` (dates, tree JSON, `--content-file`) still raise `BadParameter`,
  so an unparseable `--from` exits 2 while a reversed range exits 1. That is a
  known inconsistency, not a pattern to copy.
- **German + English.** `smart-query` keywords support both languages.
- **References name symbols, not line numbers.** A comment pointing at
  `helpers.py:855` outlived its meaning within two commits; the function name
  would not have.

## Reporting Issues

Open an issue on GitHub with:
- What you tried (command + arguments)
- What happened (error message or unexpected output)
- What you expected
- Your Logseq version and OS

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
