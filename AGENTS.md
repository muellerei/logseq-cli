# logseq-cli — Agent Reference

Instructions for AI agents and automation tools using logseq-cli.

## Setup Check

Before using logseq-cli, verify:

```bash
# 1. CLI is installed
logseq-cli --version

# 2. Logseq is running with HTTP API enabled
logseq-cli --token "YOUR_TOKEN" get-all-pages --json 2>&1 | head -1
# Success: JSON array. Failure: "Connection refused" or "401"
```

If Logseq is not running, fall back to direct filesystem access on the graph's markdown files.

## The 5 Core Workflows

### 1. Read a Page

```bash
# Human-readable
logseq-cli --token "TOKEN" get-page --name "Page Name"

# JSON (for parsing)
logseq-cli --token "TOKEN" get-page --name "Page Name" --json

# Logseq-compatible markdown (for export)
logseq-cli --token "TOKEN" get-page --name "Page Name" --format markdown --no-backlinks
```

### 2. Write to Journal

```bash
# Single entry under default heading (from LOGSEQ_JOURNAL_HEADING env var)
logseq-cli --token "TOKEN" add-journal-block --content "**14:30** Meeting notes"

# Under a specific heading
logseq-cli --token "TOKEN" add-journal-block --under-heading "## Meeting" --content "Agenda item"

# Hierarchical content (multiple nested blocks)
logseq-cli --token "TOKEN" add-journal-content --content "- ## Notes\n\t- Point 1\n\t- Point 2"

# Preview without writing
logseq-cli --token "TOKEN" add-journal-block --dry-run --content "Test entry"

# Retroactive entry (past date)
logseq-cli --token "TOKEN" add-journal-block --date 2026-04-03 --content "**14:30** Late note"

# Top-level (ignore heading env var)
logseq-cli --token "TOKEN" add-journal-block --top-level --content "Top-level block"
```

### 3. Search and Query

```bash
# Page name search
logseq-cli --token "TOKEN" search-pages --query "keyword"

# Natural language query (supports German and English)
logseq-cli --token "TOKEN" smart-query --request "open tasks"
logseq-cli --token "TOKEN" smart-query --request "offene aufgaben"
logseq-cli --token "TOKEN" smart-query --request "erledigt"

# Raw Datalog query
logseq-cli --token "TOKEN" smart-query --advanced --request '[:find (pull ?b [*]) :where [?b :block/marker "TODO"]]'

# Find backlinks
logseq-cli --token "TOKEN" get-backlinks --name "Page Name"
```

### 4. Manage TODOs

```bash
# All open tasks
logseq-cli --token "TOKEN" get-todos

# Filter by status
logseq-cli --token "TOKEN" get-todos --status TODO --status DOING

# Filter by page (substring)
logseq-cli --token "TOKEN" get-todos --page "Project Alpha"

# Filter by tag
logseq-cli --token "TOKEN" get-todos --tag urgent

# Mark as done (search & replace)
logseq-cli --token "TOKEN" replace-text --page "Page" --find "TODO Task" --replace "DONE Task" --dry-run
logseq-cli --token "TOKEN" replace-text --page "Page" --find "TODO Task" --replace "DONE Task"
```

### 5. Properties

```bash
# Read all properties
logseq-cli --token "TOKEN" get-properties --name "Page"

# Read single property
logseq-cli --token "TOKEN" get-properties --name "Page" --property status

# Set property
logseq-cli --token "TOKEN" set-property --name "Page" --key status --value Active

# Find pages by property
logseq-cli --token "TOKEN" query-pages-by-property --key type --value Person
```

## Common Gotchas

### 1. No Positional Arguments

Every parameter uses `--flag value` syntax. This is different from git, npm, and most other CLIs.

```bash
# WRONG (will fail)
logseq-cli get-page "My Page"

# CORRECT
logseq-cli --token "TOKEN" get-page --name "My Page"
```

### 2. Journal Page Naming

Logseq uses locale-specific page names for journals. The CLI handles this automatically — just pass `--date YYYY-MM-DD` or omit for today. Never construct journal page names manually.

```bash
# CORRECT: Let the CLI resolve the page name
logseq-cli --token "TOKEN" add-journal-block --date 2026-04-05 --content "Entry"

# ALSO CORRECT: Reference by resolved name
logseq-cli --token "TOKEN" get-page --name "2026-04-05, saturday"

# WRONG: Filesystem date format
logseq-cli --token "TOKEN" get-page --name "2026_04_05"
```

### 3. Block Hierarchy

Content with parent-child relationships must use tab indentation:

```bash
# Tabs for nesting (the CLI handles both tabs and 2-space indentation)
logseq-cli --token "TOKEN" add-journal-content \
  --content "- ## Section\n\t- Child item\n\t\t- Grandchild"
```

### 4. Destructive Operations

Always use `--dry-run` before `replace-text`:

```bash
# Preview first
logseq-cli --token "TOKEN" replace-text --page "Page" --find "X" --replace "Y" --dry-run

# Then execute
logseq-cli --token "TOKEN" replace-text --page "Page" --find "X" --replace "Y"
```

### 5. Connection Errors

If Logseq is not running, the CLI will print "Cannot connect to Logseq API" and exit with code 1. In this case, fall back to direct filesystem access:

- Journals: `journals/YYYY_MM_DD.md`
- Pages: `pages/Page Name.md`

## Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `LOGSEQ_JOURNAL_HEADING` | (none) | Default heading for `add-journal-block` and `add-journal-content` (e.g. `## Log`) |
| `LOGSEQ_TOKEN` | (none) | Bearer token (alternative to `--token` flag) |
| `LOGSEQ_HOST` | `127.0.0.1` | Logseq API host |
| `LOGSEQ_PORT` | `12315` | Logseq API port |
| `LOGSEQ_API_URL` | auto | Full API URL override |

## Command Summary

| Command | Use When |
|---------|----------|
| `add-journal-block` | Single journal entry (recommended) |
| `add-journal-content` | Multi-block hierarchical journal content |
| `add-journal-entry` | Deprecated — use `add-journal-block` |
| `add-note-content` | Append to non-journal pages |
| `get-page` | Read any page (text, JSON, or markdown) |
| `get-block` | Resolve block references `((uuid))` |
| `search-pages` | Find pages by name |
| `smart-query` | Natural language or Datalog queries |
| `get-todos` | List and filter tasks |
| `get-backlinks` | Find pages linking to a page |
| `insert-block` | Insert at specific position (after/before/child-of) |
| `replace-text` | Search and replace with dry-run |
| `get-properties` / `set-property` | Read/write page properties |
