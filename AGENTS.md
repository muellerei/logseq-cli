# logseq-cli: Agent Reference

Instructions for any AI agent or automation tool driving logseq-cli from a
shell. Nothing here is specific to one assistant: the CLI is a plain Python
package (`click`, `requests`) with no vendor coupling.

What makes it scriptable:

- `--json` on **every** command; payload goes to stdout, nothing else does
- errors go to **stderr**, as a JSON object when `--json` is set, so stdout can
  be parsed unconditionally
- non-zero exit on failure, including "not found"
- `--dry-run` on every command that writes, showing the state it would replace

## Setup Check

```bash
# 1. CLI is installed
logseq-cli --version

# 2. Connectivity, token, API and graph access in one call
logseq-cli --token "TOKEN" doctor --json

# 3. Put the token in the environment for every call after this one
export LOGSEQ_TOKEN="TOKEN"
```

Every example below assumes `LOGSEQ_TOKEN` is set. Pass `--token` only to
override it — for a second graph, say. Prefer the environment variable:
command-line arguments are visible to any process via `ps` and land in the
shell history.

`doctor` checks each step separately, so a failure names which one broke rather
than leaving you to guess between "Logseq is down" and "the token is wrong". It
also reports the config file, if there is one, and which `[graph]` settings it
carries — see [docs/configuration.md](docs/configuration.md).

Requires Python 3.10 or newer. `click`, `requests` and (on 3.10 only) `tomli`
are installed with the package; nothing else is needed at runtime.
Both also surface on any other command as `{"error": ..., "reason":
"connection_refused" | "http_error"}` on stderr with exit 1.

If Logseq is not running, fall back to direct filesystem access on the graph's markdown files.

## The 5 Core Workflows

### 1. Read a Page

```bash
# Human-readable
logseq-cli get-page --name "Page Name"

# JSON (for parsing)
logseq-cli get-page --name "Page Name" --json

# Logseq-compatible markdown (for export)
logseq-cli get-page --name "Page Name" --format markdown --no-backlinks
```

### 2. Write to Journal

```bash
# Single entry under default heading (from LOGSEQ_JOURNAL_HEADING env var)
logseq-cli add-journal-block --content "**14:30** Meeting notes"

# Under a specific heading
logseq-cli add-journal-block --under-heading "## Meeting" --content "Agenda item"

# Hierarchical content (multiple nested blocks)
logseq-cli add-journal-content --content "- ## Notes\n\t- Point 1\n\t- Point 2"

# Preview without writing
logseq-cli add-journal-block --dry-run --content "Test entry"

# Retroactive entry (past date)
logseq-cli add-journal-block --date 2026-04-03 --content "**14:30** Late note"

# Top-level (ignore heading env var)
logseq-cli add-journal-block --top-level --content "Top-level block"
```

### 3. Search and Query

```bash
# Page name search
logseq-cli search-pages --query "keyword"

# Natural language query (supports German and English)
logseq-cli smart-query --request "open tasks"
logseq-cli smart-query --request "offene aufgaben"
logseq-cli smart-query --request "erledigt"

# Raw Datalog query
logseq-cli smart-query --advanced --request '[:find (pull ?b [*]) :where [?b :block/marker "TODO"]]'

# Find backlinks
logseq-cli get-backlinks --name "Page Name"
```

### 4. Manage TODOs

```bash
# All open tasks
logseq-cli get-todos

# Filter by status
logseq-cli get-todos --status TODO --status DOING

# Filter by page (substring)
logseq-cli get-todos --page "Project Alpha"

# Filter by tag
logseq-cli get-todos --tag urgent

# Mark as done
logseq-cli set-todo-status --id UUID --status DONE

# ... or without knowing the UUID (aborts if the text matches several blocks)
logseq-cli set-todo-status --content "Task" --page "Page" --status DONE

# Follow a ((uuid)) reference in a journal to the original block
logseq-cli set-todo-status --id JOURNAL-REF-UUID --status DONE --follow-refs
```

Do not use `replace-text` to change a marker: it rewrites by text match, so it
also hits the word elsewhere on the page and silently retypes the rest of the
line. `set-todo-status` swaps only the marker, in one call.

### 5. Properties

```bash
# Read all properties
logseq-cli get-properties --name "Page"

# Read single property
logseq-cli get-properties --name "Page" --property status

# Set property
logseq-cli set-property --name "Page" --key status --value Active

# Find pages by property
logseq-cli query-pages-by-property --key type --value Person
```

## Common Gotchas

### 1. No Positional Arguments

Every parameter uses `--flag value` syntax. This is different from git, npm, and most other CLIs.

```bash
# WRONG (will fail)
logseq-cli get-page "My Page"

# CORRECT
logseq-cli get-page --name "My Page"
```

### 2. Journal Page Naming

Logseq uses locale-specific page names for journals. The CLI handles this automatically — just pass `--date YYYY-MM-DD` or omit for today. Never construct journal page names manually.

```bash
# CORRECT: Let the CLI resolve the page name
logseq-cli add-journal-block --date 2026-04-05 --content "Entry"

# ALSO CORRECT: Reference by resolved name
logseq-cli get-page --name "2026-04-05, saturday"

# WRONG: Filesystem date format
logseq-cli get-page --name "2026_04_05"
```

### 3. Block Hierarchy

Content with parent-child relationships must use tab indentation:

```bash
# Tabs for nesting (the CLI handles both tabs and 2-space indentation)
logseq-cli add-journal-content \
  --content "- ## Section\n\t- Child item\n\t\t- Grandchild"
```

### 4. Destructive Operations

`--dry-run` is available on every write, not only the ones that cascade:
`replace-text`, `update-block`, `remove-block`, `copy-block`, `delete-page`,
`insert-block`, `add-journal-block`, `add-journal-content`, `move-block`,
`set-todo-status`, `set-property`, `remove-property`, `set-block-property`,
`add-block-ref`, `add-note-content`, `rename-page`. Use it first.

On the in-place writes the preview shows the state that would be replaced —
the old marker, the property value about to be overwritten, or (for
`rename-page`) the pages whose `[[links]]` would be rewritten. Two of them
catch mistakes the live path cannot see at all: `set-block-property --dry-run`
fails on an unknown UUID, and `add-block-ref --dry-run` warns when the source
block does not exist, which would otherwise write a ref that renders as
nothing. Every validation still applies under `--dry-run`, so a preview that
exits 0 means the real call would too.

```bash
# Preview first
logseq-cli replace-text --page "Page" --find "X" --replace "Y" --dry-run

# Then execute
logseq-cli replace-text --page "Page" --find "X" --replace "Y"
```

To relocate a block, prefer `move-block` over `copy-block --remove`: it moves the
block itself, so its UUID and every `((block-ref))` pointing at it survive, and
nothing is deleted.

### 5. `id::` in a Tree Insert

An `id::` line inside `--tree` content names the UUID that block is meant to
keep — it appears in any outline copied out of a graph where something links to
it. Logseq only honours it when the write asks for it, so by default those ids
are dropped and the blocks land under fresh UUIDs. Every `((uuid))` elsewhere in
the graph that pointed at the originals then dangles, and Logseq rewrites such
references as plain text.

The command says how many ids it dropped, on stderr. Pass `--keep-ids` when you
are **moving or restoring** an outline:

```bash
logseq-cli insert-block --child-of UUID --tree-file outline.md --keep-ids
```

Do NOT pass it when copying an outline whose original still exists: two blocks
would share one uuid, and `((ref))` becomes ambiguous. Ids that are not valid
UUIDs abort the command before anything is written.

### 6. Connection Errors

If Logseq is not running, the CLI will print "Cannot connect to Logseq API" and exit with code 1. In this case, fall back to direct filesystem access:

- Journals: `journals/YYYY_MM_DD.md`
- Pages: `pages/Page Name.md`

## Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `LOGSEQ_JOURNAL_HEADING` | (none) | Default heading for `add-journal-block` and `add-journal-content` (e.g. `## Log`) |
| `LOGSEQ_TOKEN` | (none) | Bearer token; the documented way to pass it. `--token` overrides it |
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
| `insert-block` | Insert at specific position (after/before/child-of, `--first` for first child); `--keep-ids` preserves `id::` values in a tree |
| `find-block` | Find blocks by content; `--limit N` caps the output (what is withheld goes to stderr); `--with-children` prints the subtree |
| `update-block` | Change one block's content (by `--id` or `--where-content`); its properties are kept |
| `set-todo-status` | Change a TODO/DOING/DONE marker (never `replace-text`) |
| `move-block` | Relocate a block, keeping its UUID and refs |
| `copy-block` | Copy a block to another page (new UUID) |
| `remove-block` | Delete a block by UUID |
| `get-journal-range` | Read many journal days in one call |
| `doctor` | Check connectivity, token, API and graph access |
| `replace-text` | Search and replace with dry-run |
| `add-block-ref` | Point a `((block-ref))` at an existing block (journal or page) |
| `rename-page` | Rename a page; Logseq rewrites `[[links]]` graph-wide — preview with `--dry-run` |
| `get-properties` / `set-property` | Read/write page properties |
| `set-block-property` / `remove-property --id` | Read/write properties on a single block |
