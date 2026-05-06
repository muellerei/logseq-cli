# logseq-cli

CLI for Logseq knowledge graph with 34 commands for pages, journals, blocks, search, properties, and graph analysis.

## Installation

```bash
pip install -e .
```

Requires Python 3.10+ and a running Logseq Desktop app (HTTP API on port 12315).

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `LOGSEQ_HOST` | `127.0.0.1` | Logseq API host |
| `LOGSEQ_PORT` | `12315` | Logseq API port |
| `LOGSEQ_TOKEN` | (empty) | Bearer token for authentication |
| `LOGSEQ_API_URL` | auto | Full API URL override |
| `LOGSEQ_JOURNAL_HEADING` | (none) | Default heading for `add-journal-block` (e.g. `## Log`) |
| `LOGSEQ_CLI_CACHE_TTL` | `60` | In-memory read-cache TTL in seconds (0 = disabled) |
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

## What's new in v0.4

Seven changes focused on round-trip reduction and ergonomics. All read methods are now cached in-memory (60s TTL) and `get-journal-range` fetches in parallel.

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
LOGSEQ_CLI_CACHE_TTL=120 logseq-cli get-all-pages   # extend TTL
logseq-cli --no-cache get-page --page "X"           # bypass for one call
LOGSEQ_CLI_CACHE_TTL=0 logseq-cli ...               # disable globally
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
| `get-journal-range --from DATE --to DATE [--resolve-refs]` | Batch journal read; parallel (5 workers default) |
| `search-pages --query TEXT` | Case-insensitive name search |
| `get-backlinks --page NAME` | Pages linking to NAME |
| `get-journal-summary --range RANGE` | Journal summary (today, this week, last 30 days) |
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
| `add-journal-block --content TEXT` | Add block to journal — auto-detects hierarchical content (`--under-heading`, `--dry-run`) |
| `add-journal-content --content TEXT` | Add hierarchical content to journal (`--under-heading`, `--dry-run`) |
| `add-note-content --page NAME --content TEXT [--under-heading "## X"]` | Add content to any page; optionally under a heading (created if missing) |

### Edit (5)

| Command | Description |
|---------|-------------|
| `update-block --id UUID --content TEXT` | Update block content |
| `remove-block --id UUID` | Delete a block |
| `replace-text --page NAME --find TEXT --replace TEXT` | Search & replace with regex and dry-run support |
| `insert-block --content TEXT [--child-of UUID]` | Insert block at position (after/before/child-of/page) |
| `insert-block --tree "<tab-or-json>" [--child-of UUID \| --page NAME --top-level]` | Batch-insert a hierarchy in one call (DFS pre-order UUIDs returned) |
| `copy-block --id UUID --to-page NAME` | Copy/move block with children to another page |

### Meta (2)

| Command | Description |
|---------|-------------|
| `get-todos [--page NAME] [--status S] [--tag TAG]` | List tasks (page name shown inline in plain-text output) |
| `get-properties --page NAME [--property KEY]` | Get page properties |

### Properties (3)

| Command | Description |
|---------|-------------|
| `set-property --page NAME --key KEY --value VAL` | Set/update a page property |
| `remove-property --page NAME --key KEY` | Remove a page property |
| `set-block-property --id UUID --key KEY --value VAL` | Set/update a block property |

### Page Management (2)

| Command | Description |
|---------|-------------|
| `rename-page --page NAME --new-name NAME` | Rename page (updates all references) |
| `delete-page --page NAME [--force]` | Delete page (with confirmation prompt) |

### Query (1)

| Command | Description |
|---------|-------------|
| `query-pages-by-property --key KEY [--value VAL]` | Find pages by property value |

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
│   └── cli.py       # Click CLI with all 34 commands
├── examples/        # Shell scripts for scripting/cronjobs
└── pyproject.toml
```

The CLI communicates with Logseq's built-in HTTP API (Fastify server on port 12315).
The core commands are inspired by [joelhooks/logseq-mcp-tools](https://github.com/joelhooks/logseq-mcp-tools), extended with property management, page operations, and property-based queries.
