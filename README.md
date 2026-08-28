# logseq-cli

CLI for Logseq knowledge graph: pages, journals, blocks, search, properties, and graph analysis.

## Using this from an agent

The CLI is built to be driven by scripts and AI agents: `--json` on every
command, payload on stdout, errors as JSON on stderr, non-zero exit on
failure, `--dry-run` on everything destructive. No vendor coupling: it is a
plain Python package with `click` and `requests`.

See [AGENTS.md](AGENTS.md) for the workflows and gotchas.

## Installation

```bash
pip install -e .
```

Requires Python 3.10+ and a running Logseq Desktop app (HTTP API on port 12315).
Developed and tested against Logseq Desktop 0.10.15 with a file-based graph;
newer Logseq versions are untested.

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `LOGSEQ_HOST` | `127.0.0.1` | Logseq API host |
| `LOGSEQ_PORT` | `12315` | Logseq API port |
| `LOGSEQ_TOKEN` | (empty) | Bearer token for authentication |
| `LOGSEQ_API_URL` | auto | Full API URL override |
| `LOGSEQ_JOURNAL_HEADING` | (none) | Default heading for `add-journal-block` (e.g. `## Log`) |
| `LOGSEQ_CLI_CACHE_TTL` | `60` | In-memory read-cache TTL in seconds (0 = disabled). Per process, not shared between invocations |
| `LOGSEQ_CLI_RANGE_WORKERS` | `5` | Parallel workers for `get-journal-range` (1–16) |

All connection settings can also be passed as CLI flags: `--host`, `--port`, `--token`.

### Journal heading

By default, `add-journal-block` appends blocks at the top level of the journal page. Set `LOGSEQ_JOURNAL_HEADING` to automatically insert blocks under a specific heading:

```bash
# In your shell profile (~/.zshrc, ~/.bashrc, etc.)
export LOGSEQ_JOURNAL_HEADING="## Log"
```

This can be overridden per call:

```bash
# Use a different heading
logseq-cli add-journal-block --under-heading "## Notes" --content "..."

# Force top-level (ignore env var)
logseq-cli add-journal-block --top-level --content "..."
```

## What's new in v0.6

Safety and output-size work from an audit against common CLI conventions
(clig.dev, POSIX/grep, agent tool-design guidance). **Defaults are
unchanged** — without the new flags every command behaves exactly as before.

```bash
# 1. --dry-run for the destructive commands (they cascade: children, source blocks)
logseq-cli remove-block --id "$UUID" --dry-run
#   [DRY RUN] Would remove block 6a76533e-...
#     descendants that would be removed too: 2
#     total blocks affected: 3
logseq-cli update-block --id "$UUID" --content "neu" --dry-run
logseq-cli copy-block --id "$UUID" --to-page "Target" --remove --dry-run
logseq-cli delete-page --page "Alt" --dry-run

# 2. delete-page: --json is no longer an implicit --force
logseq-cli delete-page --page "Alt" --json < /dev/null
#   {"error": "Refusing to delete page 'Alt' non-interactively without --force. ..."}
#   exit 1 — the output format no longer doubles as a confirmation.

# 3. get-page separates "missing" from "empty"
logseq-cli get-page --page "Tippfehler"   # (page does not exist) -> exit 1
logseq-cli get-page --page "Leere Seite"  # (empty page)          -> exit 0

# 4. Bounded journal reads (see "Bounded output" below)
logseq-cli get-journal-range --from 2026-07-08 --to 2026-08-07 \
  --tail 7 --heading "## Log"
logseq-cli get-journal-summary --range "this week" --no-content

# 5. delete-block works as an alias for remove-block
logseq-cli delete-block --id "$UUID" --dry-run

# 6. Errors are JSON when --json is set — always on stderr, never on stdout
logseq-cli get-properties --page "Missing" --json 2>err.json
```

## What's new in v0.4

Seven changes focused on round-trip reduction and ergonomics. All read methods are cached in-memory for the duration of one process (60s TTL), and `get-journal-range` fetches in parallel.

```bash
# 1. Inline ((uuid)) refs while reading — no more N×get-block round-trips
logseq-cli get-page --page "2026-04-22, wednesday" --resolve-refs

# 2. UUID prefix per block line — replaces --json | jq pipelines
logseq-cli get-page --page "Project Alpha" --with-ids
# <uuid>\t<indent>\t<content>

# 3. get-todos shows page inline (plain-text); JSON unchanged
logseq-cli get-todos --status TODO
#   TODO [Project Alpha] Tag-Support GUI fertigstellen
#   DOING [2026-04-22, wednesday] 1:1 Bob vorbereiten

# 4. insert-block --tree: batch-insert a hierarchy in one call
logseq-cli insert-block --child-of "$UUID" --tree "### Meeting
	- Agenda
	- Outcome
		- Detail"
# Or as JSON:
logseq-cli insert-block --page "Project" --top-level --tree \
  '[{"content":"### Section","children":[{"content":"Item"}]}]'

# 5. add-note-content --under-heading: heading-aware insertion for non-journal pages
logseq-cli add-note-content --page "Project" --under-heading "## Notes" \
  --content "- New observation\n\t- Detail"
# Heading is created if missing.

# 6. In-memory read cache (60s TTL by default)
# Scope: ONE process. Two shell invocations do not share it, so a second
# `logseq-cli get-page X` hits the API again. It pays off inside a single call
# that reads repeatedly: --name A --name B, get-journal-range, --resolve-refs.
logseq-cli --no-cache get-page --page "X"           # bypass for one call
LOGSEQ_CLI_CACHE_TTL=0 logseq-cli ...               # disable
LOGSEQ_CLI_CACHE_TTL=120 logseq-cli get-journal-range --from ... --to ...
# Mutations (insert/update/remove/createPage/...) invalidate the cache.

# 7. Parallel pool for get-journal-range (5 workers default)
LOGSEQ_CLI_RANGE_WORKERS=10 logseq-cli get-journal-range \
  --from 2026-01-01 --to 2026-04-30 --resolve-refs
# Order is stable (sorted by date). Per-day errors embed an `error` field
# and the range continues.
```

## Usage

```bash
# List all pages
logseq-cli get-all-pages

# Get page content with backlinks
logseq-cli get-page --page "My Page"

# Search pages
logseq-cli search-pages --query "Import"

# Write to today's journal (under configured heading)
logseq-cli add-journal-block --content "**14:30** Meeting notes"

# Preview before writing
logseq-cli add-journal-block --dry-run --content "**14:30** Meeting notes"

# Write content from a file — no shell quoting, several flush "- " roots allowed
logseq-cli add-journal-block --content-file entry.md

# Export page as Logseq-compatible markdown
logseq-cli get-page --page "My Page" --format markdown

# Write hierarchical content
logseq-cli add-journal-content --content "- ## Notes\n\t- Item 1\n\t- Item 2"

# JSON output for piping
logseq-cli get-all-pages --json | jq length

# Check version
logseq-cli --version
```

### Parameter aliases

All commands that take a page name accept both `--page` and `--name`:

```bash
logseq-cli get-page --page "My Page"
logseq-cli get-page --name "My Page"   # equivalent
```

## Commands

### Read (13)

| Command | Description |
|---------|-------------|
| `get-all-pages` | List all pages |
| `get-page --page NAME [--resolve-refs] [--with-ids] [--format markdown]` | Page content with backlinks; optionally inline `((uuid))` refs or prefix UUIDs per line |
| `get-block --id UUID` | Block by UUID |
| `find-block --content TEXT [--page NAME] [--regex] [--first] [--with-children]` | Find blocks by content. `--with-children` prints each match with its sub-blocks indented, instead of guessing a line count with `get-page \| grep -A<n>`; costs one extra read per match, capped at 25 with the remainder reported |
| `get-journal-range --from DATE --to DATE [--resolve-refs] [--tail N] [--limit N] [--heading "## Log"]` | Batch journal read; parallel (5 workers default). `--tail/--limit/--heading` bound the output — see [Bounded output](#bounded-output) |
| `search-pages --query TEXT` | Case-insensitive name search |
| `get-backlinks --page NAME` | Pages linking to NAME |
| `get-journal-summary --range RANGE [--no-content]` | Journal summary (today, this week, last 30 days). `--no-content` drops the per-day bodies |
| `analyze-graph [--days N]` | Graph structure analysis |
| `find-knowledge-gaps` | Missing/underdeveloped/orphaned pages |
| `analyze-journal-patterns` | Journal entry patterns |
| `smart-query --request TEXT` | Datalog queries (natural language or `--advanced` for raw Datalog) |
| `suggest-connections` | Topic-based connection suggestions |
| `get-page-stats --page NAME` | Page statistics (blocks, words, in/outbound links) |

### Write (5)

| Command | Description |
|---------|-------------|
| `create-page --name NAME` | Create a new page |
| `add-journal-entry --content TEXT` | Add journal entry (deprecated, use add-journal-block) |
| `add-journal-block --content TEXT` | Add block to journal — auto-detects hierarchical content (`--under-heading`, `--dry-run`). `--content-file FILE` reads the whole file as one tree: no shell quoting, flush `- ` lines become sibling roots |
| `add-journal-content --content TEXT` | Add hierarchical content to journal (`--under-heading`, `--dry-run`) |
| `add-note-content --page NAME --content TEXT [--under-heading "## X"]` | Add content to any page; optionally under a heading (created if missing) |

### Edit (5)

| Command | Description |
|---------|-------------|
| `update-block (--id UUID \| --where-content TEXT [--page NAME] [--regex]) --content TEXT [--dry-run]` | Update block content. `--content` is ONE block and has no tree path: newline bullets are rejected, indented ones too: use `insert-block --child-of` for children. `--where-content` selects by text instead of UUID and aborts unless exactly one block matches |
| `remove-block --id UUID [--dry-run]` | Delete a block and its children (alias: `delete-block`). `--dry-run` reports the descendant count |
| `add-block-ref --source-id UUID (--journal-date DATE \| --page NAME) [--under-heading "## X"]` | Write a `((block-ref))` pointing at an existing block. Journal defaults to today, heading to `LOGSEQ_JOURNAL_HEADING` |
| `set-todo-status (--id UUID \| --content TEXT --page NAME) --status DONE [--follow-refs]` | Swap a TODO/DOING/DONE marker without retyping the line. `--follow-refs` updates the original when the block is just a `((ref))`. Ambiguous `--content` aborts and lists candidates |
| `replace-text --page NAME --find TEXT --replace TEXT` | Search & replace with regex and dry-run support |
| `insert-block --content TEXT [--child-of UUID]` | Insert block at position (after/before/child-of/page) |
| `insert-block --tree "<tab-or-json>" [--quiet]` | `--quiet` prints only the confirmation line, not one uuid line per block |
| `insert-block --child-of UUID --first` | Insert as FIRST child instead of appending last (works with `--content` and `--tree`; order preserved). Only valid with `--child-of` |
| `insert-block --tree "<tab-or-json>" [--child-of UUID \| --page NAME --top-level]` | Batch-insert a hierarchy in one call (DFS pre-order UUIDs returned). `--tree-file FILE` reads the same tab-indented text or JSON from a file |
| `copy-block --id UUID --to-page NAME [--remove] [--dry-run]` | Copy/move block with children to another page |
| `move-block --id UUID (--under UUID \| --before UUID) [--dry-run]` | Structural move: the block keeps its UUID, so `((block-refs))` to it survive. Prefer over `copy-block --remove`, which writes a new block and deletes the original. `--under` nests as first child, `--before` places it in front as a sibling |

### Meta (2)

| Command | Description |
|---------|-------------|
| `get-todos [--page NAME] [--status S] [--tag TAG]` | List tasks (page name shown inline in plain-text output) |
| `get-properties --page NAME [--property KEY]` | Get page properties |
| `doctor` | Health-check: connectivity, token, API, graph. Exit 0 = ready |

### Properties (3)

| Command | Description |
|---------|-------------|
| `set-property --page NAME --key KEY --value VAL` | Set/update a page property |
| `remove-property (--page NAME \| --id UUID) --key KEY` | Remove a page property. `--id` targets a single block instead of the page |
| `set-block-property --id UUID --key KEY --value VAL` | Set/update a block property |

### Page Management (2)

| Command | Description |
|---------|-------------|
| `rename-page --page NAME --new-name NAME` | Rename page (updates all references) |
| `delete-page --page NAME [--force] [--dry-run]` | Delete page. Prompts on a TTY; `--force` required non-interactively |

### Query (1)

| Command | Description |
|---------|-------------|
| `query-pages-by-property --key KEY [--value VAL]` | Find pages by property value |

## Bounded output

Journal reads grow with the range. On a real graph (four years of daily entries)
the unbounded commands produce far more text than an LLM agent can hold:

| Call | Output |
|------|--------|
| `get-journal-range` over 30 days | 431,996 chars (~108k tokens) |
| `get-journal-range --tail 7` | 188,149 chars |
| `get-journal-range --tail 7 --heading "## Log"` | 136,289 chars |
| `get-journal-summary --range "this week"` | 143,733 chars |
| `get-journal-summary --range "this week" --no-content` | 793 chars |

For scale: Claude Code caps tool responses at 25,000 tokens by default.

- `--tail N` / `--limit N` pick the newest / oldest N journal days. They are
  applied **before** fetching, so omitted days cost no API call. Mutually
  exclusive.
- `--heading "## Log"` returns only that section per day.
- `--no-content` (summary only) drops the bodies but keeps date, character
  count, topics and top concepts — enough for an overview, without the text.

Truncation is never silent: whenever days are omitted, a note goes to **stderr**
(`showing 3 of 20 journal day(s) ... 17 omitted`) while stdout stays pure
payload. Without truncation there is no note.

## Internationalization

`smart-query` supports both English and German keywords for template matching:

```bash
logseq-cli smart-query --request "offene aufgaben"   # German → finds open tasks
logseq-cli smart-query --request "open tasks"         # English → same result
```

Date formatting is locale-independent — weekday and month names are always English (as Logseq expects), regardless of system locale.

## Scripting Examples

See `examples/` directory:

- `export-all-pages.sh` - Batch export all pages as JSON
- `daily-todos.sh` - Daily TODO overview (suitable for cronjob)
- `top-pages-pipeline.sh` - Pipeline with jq for graph statistics

## Architecture

```
logseq-cli/
├── logseq_cli/
│   ├── api.py       # HTTP API client (requests.post against Logseq)
│   ├── helpers.py   # Date parsing, block processing, backlink search
│   └── cli.py       # Click CLI with all commands
├── examples/        # Shell scripts for scripting/cronjobs
└── pyproject.toml
```

The CLI communicates with Logseq's built-in HTTP API (Fastify server on port 12315).
The core commands are inspired by [joelhooks/logseq-mcp-tools](https://github.com/joelhooks/logseq-mcp-tools), extended with property management, page operations, and property-based queries.
