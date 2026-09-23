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

# Large page: outline first (headings + uuids, nested as the sections are),
# then one section
logseq-cli get-page --name "Page Name" --outline
logseq-cli get-page --name "Page Name" --heading "## Log"

# Cut to size; stderr names what was withheld and the block to continue at
logseq-cli get-page --name "Page Name" --max-chars 20000
logseq-cli get-page --name "Page Name" --max-chars 20000 --from-block <uuid>
```

Do not cut a read with `head`: the cut lands mid-block and nothing says what
is missing. `--max-chars` cuts between blocks and reports the rest. It counts
what is printed, so `--json` reaches it several times sooner. Repeat the
command with `--from-block` and the uuid from the note until the note names
no `--from-block`. A note that names the `--max-chars` a block needs instead
ends the chain: raise the cap, or read that block alone with
`get-block --id <uuid> --no-children`. The same
two flags work on `get-journal-range`.

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

Text with apostrophes, quotes or umlauts is safer from a file than through
shell quoting: on `update-block`, `insert-block`, `add-note-content` and
`add-journal-content`, `--content-file FILE` (`-` reads stdin) is `--content`
read from the file, with the same rules. `create-page` and the deprecated
`add-journal-entry` do not have it. On `add-journal-block` the file is read as one
tree instead, so flush `- ` lines become sibling blocks.

```bash
logseq-cli update-block --id "$U" --content-file note.md
printf 'Alice'"'"'s note\n' | logseq-cli insert-block --child-of "$U" --content-file -
```

`add-journal-block`, `add-journal-content`, `add-note-content` and
`add-block-ref` print the uuid of the block they wrote (the root, for a tree)
on one `  uuid: ...` line, so a follow-up `find-block` to recover it is not
needed. `insert-block` prints one such line per block, root first, unless
`--quiet`.

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

# The uuid of the one block to write to; exits 1 on no match and on several
# (--first would pick one of several without a word on stdout)
U=$(logseq-cli find-block --content "tag support" --page "Project Alpha" --exactly-one --uuid-only)
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

# Filter by what the task says (regex, case-insensitive)
logseq-cli get-todos --match "review|contract" --json
# --json: {"todos": [{marker, content, page, uuid, journal_day?, ...}], "count": N}

# Tasks standing in a date range, including ones carried forward by ((block-ref))
logseq-cli get-todos --from 2026-09-14 --to 2026-09-16 --json

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

# Set property. Keys are stored as Logseq reads them back: "Status" becomes
# "status" (noted on stderr); a key Logseq would drop is refused, exit 1.
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

Each line is a block, with two exceptions that stay with the block they belong
to, as in Logseq's own files. A `key:: value` line without a bullet goes on the
block above. So does a code block: from a line starting with ```` ``` ```` or
`~~~` to the next such line without a bullet, every line is code, `- ` lines
included, and keeps its indentation. Put the fence on a bullet line to make the
code block a block of its own:

```bash
logseq-cli add-note-content --page "Notes" \
  --content $'- Example\n  ```js\n  run()\n  ```\n- ```sh\n  make\n  ```'
# -> "Example" with the code on it, then a block holding only the sh code
```

Text the CLI writes as ONE block must come back from the page file as that
block. Logseq's file parser takes a `- ` or `#` line after the first (indented
too) and a code fence nothing closes as block boundaries, and rebuilds the page
the next time it reads the file: the lines become blocks of their own, and an
unclosed fence swallows the blocks after it. So every such write is refused
before anything is written, with the line and the way to write it: flat
`--content`, a JSON `--tree` node, `update-block`, `create-page --content`,
also `copy-block`, `replace-text` and the heading of `--under-heading`. Inside
a closed code block these lines are fine. Indent sub-bullets to write children,
or pass `--content` several times (`add-journal-block`) for blocks side by
side. `--json` gives `{"reason": "splits_into_blocks", "line": N, "kind": ...}`
with exit code 2.

A property value (`set-property`, `set-block-property`, `--property`) is one
line: Logseq writes it into the block as `key:: value`, so a line break in it
is refused. A change to part of a block (`replace-text`, `set-todo-status`)
may leave as many such lines as the block had, and change their text, since
Logseq's own editor makes such blocks; it may not add one. `update-block`
replaces the whole text and gets no such pass.

A quote ends at a blank line. `> first`, an empty line, then `second` is one
block, but Logseq shows only the first paragraph as a quote and `second` as
plain text. Nothing is lost, so this is not refused: the write goes through
and says so on stderr with a `Note:`, naming the block and the line. To keep
a paragraph in the quote, start the blank line before it with `>` (a `>` on a
line of its own *after* the blank line does not help). The note comes after
every check that can refuse, so a refusal under `--json` stays one JSON
object. It is given where the caller sends a block's text: `update-block`,
`insert-block`, `add-journal-block`, `create-page --content`. Outline text
(`add-note-content`, `add-journal-content`) puts every line in a block of its
own and has nothing to note; `replace-text` and `set-todo-status` change text
already in the graph, where a note could not tell a quote the change made
from one that was there.

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

`remove-block`, `delete-page` and `copy-block --remove` refuse while
`((block-refs))` from elsewhere point into what they would delete, and list
where each one comes from (page, block uuid, target). `--force` does not
override this; `--ignore-refs` does, and only after the refs have been dealt
with or deliberately given up. Refs from inside the deleted set do not count,
except for `copy-block --remove`: the copy carries them under new uuids.

### 5. `id::` in Written Content

An `id::` line inside written content names the UUID that block is meant to
keep — it appears in any outline copied out of a graph where something links to
it. Logseq only honours it when the write asks for it, so by default those ids
are dropped and the blocks land under fresh UUIDs. Every `((uuid))` elsewhere in
the graph that pointed at the originals then dangles, and Logseq rewrites such
references as plain text.

This holds for `insert-block` (`--tree` and `--content`), `add-note-content`,
`add-journal-block` and `add-journal-content`; not for `create-page --content`
or the deprecated `add-journal-entry`. Each removes the `id::` lines from the
content and says how many ids it dropped, on stderr. An `id::` line inside a
code block (between two lines that start with ```` ``` ```` or `~~~`) is code, as
it is to Logseq, and is written as is. Pass
`--keep-ids` when you are **moving or restoring** an outline:

```bash
logseq-cli insert-block --child-of UUID --tree-file outline.md --keep-ids
```

Before anything is written, `--keep-ids` refuses four kinds of id: one that is
repeated in the content; a second `id::` line in the same block; one that is
not a valid UUID; and one a block or page still has (the copy case, where two blocks would share one uuid; drop the flag to copy
with new ids, or use `move-block`). An id that survives only as the target of a
`((ref))` elsewhere is restored: that is the restore case, a deleted block
written back from a copy, and the refs resolve again.
`add-journal-block` rejects the flag with `--upsert-heading`, which rewrites a
block that already exists (that block keeps its uuid), and with
`--no-preserve`, which joins the lines and turns `id::` into plain text.

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
| `get-todos` | List and filter tasks; a task carried forward by `((block-ref))` is found on the day it stands and stays one row |
| `get-backlinks` | Find pages linking to a page |
| `insert-block` | Insert at specific position (after/before/child-of, `--first` for first child); `--keep-ids` preserves `id::` values in `--tree` or `--content` (also on `add-note-content`, `add-journal-block`, `add-journal-content`) |
| `find-block` | Find blocks by content; `--limit N` caps the output (what is withheld goes to stderr); `--with-children` prints the subtree; `--uuid-only` prints bare uuids and fails on no match; `--exactly-one` fails unless exactly one block matches |
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
