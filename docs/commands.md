# Command reference

Every command, its options, and the conventions they share. The
[README](../README.md) says what the tool is for and how to start; this is
where to look something up. `logseq-cli <command> --help` has the same
options with each one's full text.

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

# Or straight from a pipe — "-" reads stdin
printf '**14:30** Notes\n\t- detail\n' | logseq-cli add-journal-block --content-file -

# Export page as Logseq-compatible markdown
logseq-cli get-page --page "My Page" --format markdown

# Write hierarchical content
logseq-cli add-journal-content --content "- ## Notes\n\t- Item 1\n\t- Item 2"

# A code block in it stays one block (on the block above, or on its own bullet)
logseq-cli add-note-content --page "Notes" --content $'- Example\n  ```js\n  run()\n  ```'

# JSON output for piping
logseq-cli get-all-pages --json | jq length

# Check version
logseq-cli --version
```

### Global options

Given before the command name (`logseq-cli --read-only add-journal-block …`):

| Option | Effect |
|--------|--------|
| `--host`, `--port`, `--token` | Where Logseq's HTTP API is and how to authenticate; each has an environment variable ([configuration.md](configuration.md#environment-variables-and-flags)) |
| `--no-cache` | Bypass the in-memory read cache for this call |
| `--read-only` | Refuse every command that writes, `--dry-run` included, for this call. Can only tighten: nothing loosens a `[safety] read_only = true` in the config. See [safety.md](safety.md) |
| `--version` | Print the version |

### Parameter aliases

All commands that take a page name accept both `--page` and `--name`:

```bash
logseq-cli get-page --page "My Page"
logseq-cli get-page --name "My Page"   # equivalent
```

### Page aliases

A page name means the page Logseq would open for it. An alias from a page's
`alias::` property reads and writes that page, in every command that names a
page: Logseq's HTTP API does not resolve an alias, and without this a read of
one came back empty and a write landed on a page of its own. With `--json` a
result that names the page keeps the name you gave in `page` and adds
`alias_of` with the page used; in text a `Note:` says so. An alias two pages
claim is refused, naming both. `delete-page` and `rename-page` take the page's
own name. An alias that has blocks of its own is a page, as in Logseq.
Filters on page names (`get-todos --page`,
`suggest-connections --focus`) match the text as given. On the unsupported DB
version the alias is not followed: the lookup relies on fields of the file
graph.

## Commands

### Read (14)

| Command | Description |
|---------|-------------|
| `get-all-pages` | List all pages |
| `get-page --page NAME [--no-backlinks] [--resolve-refs] [--with-ids] [--heading "## X"] [--outline] [--max-chars N] [--from-block UUID] [--format markdown]` | Page content with backlinks; optionally inline `((uuid))` refs or prefix UUIDs per line. `--no-backlinks` skips the backlink lookup, `--heading` returns only that section (searched recursively) and fails when the page has no such heading. `--outline` lists the headings with their uuids; `--max-chars` cuts the output to size and `--from-block` continues it — see [Bounded output](../README.md#bounded-output). With `--resolve-refs`, a ref whose target was deleted is named on stderr — on stdout it renders exactly like an unresolved one |
| `get-block --id UUID [--no-children]` | Block by UUID; `--no-children` returns the block alone. Text output starts with `Page: <name>` and `Parent: <uuid>`, or `Parent: (page)` directly under the page; the uuid works as the next `--id`. `--json` is Logseq's answer as it comes, with page and parent as database ids |
| `find-block --content TEXT [--page NAME] [--regex] [--first \| --limit N \| --exactly-one] [--with-children \| --uuid-only]` | Find blocks by content. `--uuid-only` prints bare uuids, one per line, and fails on no match. `--exactly-one` fails unless exactly one block matches and lists the matches otherwise; use it for a block to write to, `U=$(... --exactly-one --uuid-only)`, where `--first` would pick one of several. A common word matches thousands of blocks, so `--limit N` caps the output and the number withheld goes to stderr; `--first` is the same with N=1. `--with-children` prints each match with its sub-blocks indented, instead of guessing a line count with `get-page \| grep -A<n>`; costs one extra read per match, capped at 25 with the remainder reported |
| `get-journal-range --from DATE --to DATE [--resolve-refs] [--tail N] [--limit N] [--heading "## Log"] [--max-chars N] [--from-block UUID]` | Batch journal read; parallel (5 workers default). `--tail/--limit/--heading/--max-chars` bound the output, `--from-block` continues a cut one — see [Bounded output](../README.md#bounded-output) |
| `search-pages --query TEXT` | Case-insensitive name search |
| `get-backlinks --page NAME [--with-context] [--limit N]` | Pages linking to NAME. `--with-context` also shows the blocks that do the linking — they arrive with the same API call, so it costs no extra read; `--limit` (default 3) caps the blocks per page and reports the remainder; `0` keeps all |
| `get-journal-summary --range RANGE [--no-content]` | Journal summary (today, this week, last 30 days). `--no-content` drops the per-day bodies |
| `analyze-graph [--days N]` | Graph structure analysis. `--days N` limits only the "Recently updated" list to the pages modified in the last N days; every other figure covers the whole graph. See "analyze-graph output" below the table |
| `find-knowledge-gaps [--min-refs N] [--include-orphans/--no-include-orphans]` | Missing/underdeveloped/orphaned pages. `--min-refs` (default 2) is how many incoming references a short page needs before it counts as underdeveloped rather than unused |
| `analyze-journal-patterns [--timeframe RANGE] [--mood/--no-mood] [--topics/--no-topics]` | Journal entry patterns over `--timeframe` (default "last 30 days"). `--no-mood` and `--no-topics` drop those sections |
| `smart-query --request TEXT [--advanced] [--include-query]` | Datalog queries (natural language, or `--advanced` to pass raw Datalog through). `--include-query` prints the generated query alongside the result |
| `suggest-connections [--min-confidence N] [--min-shared N] [--max-suggestions N] [--focus PAGE]` | Topic-based connection suggestions. `--min-shared` (default 3) is the real filter — it sets how many topics two pages must share before the pair counts at all; `--min-confidence` (default 0.3) then scores it. `--max-suggestions` (default 10) caps the list and the remainder is reported as withheld; `total_found` counts what the graph held, not what survived the cap. `--focus` restricts to one page |
| `get-page-stats --page NAME` | Page statistics (blocks, words, in/outbound links) |

**analyze-graph output.** `--json` carries `tasks`: `{"open": N, "done": N, "cancelled": N, "open_by_marker": {"<MARKER>": N, ...}}`. A task is a block with a marker Logseq reads; `TODO:`, a lower-case `todo`, `[ ]` at the start of a block and `TODO` followed directly by a line break are not. `open` is the same number as the `count` of `get-todos --no-follow-refs`. The text output has the line `Tasks: 12 open (2 DOING, 10 TODO), 40 done, 1 cancelled`, open markers in the order of the list, and a note on stderr when blocks start with `[ ]`: `Note: N blocks start with "[ ]", which Logseq shows as text, not as a task or checkbox.`

### Write (5)

| Command | Description |
|---------|-------------|
| `create-page --name NAME [--content TEXT] [--dry-run]` | Create a new page. Fails if it already exists, rather than appending `--content` to what is there; `--dry-run` reports which of the two a run would be. An `id::` line in `--content` is dropped with a note |
| `add-journal-entry --content TEXT [--date DATE] [--multi-block] [--dry-run]` | Add journal entry (deprecated, use add-journal-block). `--date` defaults to today; `--multi-block` splits multi-line content into one block per line. An `id::` line in `--content` is dropped with a note |
| `add-journal-block --content TEXT [--date DATE] [--upsert-heading "### X"] [--no-preserve] [--keep-ids]` | Add block to journal — auto-detects hierarchical content (`--under-heading`, `--top-level`, `--dry-run`). `--date` defaults to today. `--upsert-heading` updates a matching child block under `--under-heading` instead of adding a second one; the block keeps its properties, as with `update-block`. `--content-file FILE` reads the whole file as one tree: no shell quoting, flush `- ` lines become sibling roots; `--content-file -` reads stdin. On `update-block`, `insert-block`, `add-note-content` and `add-journal-content`, `--content-file` is `--content` read from a file, with the same rules |
| `add-journal-content (--content TEXT \| --content-file FILE) [--date DATE] [--keep-ids]` | Add hierarchical content to journal (`--under-heading`, `--top-level`, `--dry-run`). `--date` defaults to today |
| `add-note-content --page NAME (--content TEXT \| --content-file FILE) [--under-heading "## X"] [--no-create] [--property K=V] [--keep-ids] [--dry-run]` | Add content to any page; optionally under a heading (created if missing). The page is created when missing unless `--no-create` is given. `--property` sets `key:: value` on the root block, repeatable. `--dry-run` reports the target, the block count and whether page or heading would be created |

A write that ends a section (`add-journal-block`, `add-journal-content`,
`add-note-content`, `add-block-ref`, `copy-block`, `insert-block --child-of` and
`--page`) goes before the empty blocks that end it, when
[`[graph] keep_empty_blocks_last`](configuration.md#graph-keep_empty_blocks_last) is on.

### Edit (8)

| Command | Description |
|---------|-------------|
| `update-block (--id UUID \| --where-content TEXT [--page NAME] [--regex]) (--content TEXT \| --content-file FILE) [--dry-run]` | Update block content. `--content` is ONE block and has no tree path: a `- ` or `# ` line after the first (indented too) or a code fence nothing closes is refused, since Logseq would read it as a block of its own: use `insert-block --child-of` for children. `--where-content` selects by text instead of UUID and aborts unless exactly one block matches. The block's properties are kept as written, values as their original text, unless `--content` sets the key itself; the one change is Logseq's own spelling of a key (`created_at` is stored and written back as `created-at`). The block's own `id::` line passes; one naming another uuid is dropped with a note, since it would become this block's uuid |
| `remove-block --id UUID [--ignore-refs] [--dry-run]` | Delete a block and its children (alias: `delete-block`). `--dry-run` reports the descendant count. Refuses while `((block-refs))` from elsewhere point into the block or its children, and lists them; `--ignore-refs` removes anyway |
| `add-block-ref --source-id UUID (--journal-date DATE \| --page NAME) [--under-heading "## X"] [--dry-run]` | Write a `((block-ref))` pointing at an existing block. Journal defaults to today, heading to `LOGSEQ_JOURNAL_HEADING`. A source uuid no block has is refused before anything is written, `--dry-run` included — a ref to it would render as nothing. A source without an `id::` line gets one, as Logseq's editor gives it; every command that writes a `((uuid))` does this |
| `set-todo-status (--id UUID \| --content TEXT --page NAME) --status DONE [--follow-refs] [--dry-run]` | Swap a task's marker without retyping the line. `--follow-refs` updates the original when the block is just a `((ref))` or `{{embed ((ref))}}`, through a chain of them; a chain that loops or reaches a missing block is refused. Ambiguous `--content` aborts and lists candidates. A block Logseq reads no marker in (`todo x`, `TODO:`, `TODO` followed directly by a line break, a block that only holds a ref) is refused, nothing written; `--content` matching blocks without a task is refused too, unless `--follow-refs` is given. Each refusal carries a `reason` under `--json`. `--dry-run` shows the old and new marker |
| `replace-text --page NAME --find TEXT --replace TEXT` | Search & replace with regex and dry-run support. `--replace` is literal; with `--regex` it takes `\1` group references. A replacement that turns a line into an `id::` line is refused before any block is written |
| `insert-block (--content TEXT \| --content-file FILE) (--page NAME \| --after UUID \| --before UUID \| --child-of UUID)` | Insert one block at a position: appended to a page, as a sibling after or before a block, or as a child. `--property K=V` sets properties on it, repeatable. Without indentation `--content` is one block, and a line Logseq would read as a block of its own is refused before anything is written: a `- ` or `# ` line after the first, or a code fence nothing closes (fine inside a closed code block). The same holds for every write of one block's text: a `--tree` node, `add-journal-block`, `update-block`, `create-page --content`. A quote followed by a blank line and a paragraph is written, with a `Note:` on stderr: Logseq ends the quote at the blank line |
| `insert-block --tree "<tab-or-json>" [--quiet]` | `--quiet` prints only the confirmation line, not one uuid line per block |
| `insert-block --child-of UUID --first` | Insert as FIRST child instead of appending last (works with `--content` and `--tree`; order preserved). Only valid with `--child-of` |
| `insert-block --tree "<tab-or-json>" [--child-of UUID \| --page NAME --top-level]` | Batch-insert a hierarchy in one call (DFS pre-order UUIDs returned). `--tree-file FILE` reads the same tab-indented text or JSON from a file |
| `insert-block --tree ... --keep-ids` | Keep the `id::` values in the content instead of letting Logseq mint new ones, for moving or restoring an outline — top-level blocks included. Without it they are dropped and the count is reported on stderr; content that is nothing but `id::` lines is refused, since nothing would be left to write. With it, an id a block or page still has is refused before anything is written (a copy would put one uuid on two blocks), and so is a second `id::` line in one block. One that survives only as a `((ref))` target is restored: the block takes over the placeholder Logseq keeps under that uuid, and the ref resolves again. An `id::` line inside a code block (```` ``` ```` or `~~~`) is code and is written as is. The same flag and rule apply to `--content` and to `add-note-content`, `add-journal-block` and `add-journal-content` |
| `copy-block --id UUID --to-page NAME [--remove] [--ignore-refs] [--dry-run]` | Copy/move block with children to another page. The copy gets new UUIDs and leaves the source's `id::` lines out, so `--remove` refuses while `((block-refs))` point into the source (`--ignore-refs` overrides); `move-block` keeps them |
| `move-block --id UUID (--under UUID \| --before UUID) [--dry-run]` | Structural move: the block keeps its UUID, so `((block-refs))` to it survive. Prefer over `copy-block --remove`, which writes a new block and deletes the original. `--under` nests as first child, `--before` places it in front as a sibling |

### Meta (4)

| Command | Description |
|---------|-------------|
| `get-todos [--page NAME] [--state open\|done\|cancelled] [--status MARKER] [--tag TAG] [--match REGEX] [--from DATE] [--to DATE] [--due-from DATE] [--due-to DATE] [--refs-limit N] [--no-follow-refs]` | List tasks (page name shown inline in plain-text output). Without `--state` or `--status` it lists every open task, `WAIT`, `WAITING`, `IN-PROGRESS` and `STARTED` included. `--state` (repeatable: `open`, `done`, `cancelled`, default `open`) selects by state; `--status` (repeatable, case does not matter, any marker Logseq reads) selects single markers instead and wins when both are given, with a note on stderr. Tasks come ordered by marker, open work first and finished tasks last, then by page. `--from/--to` date a task by every journal it stands in, the page its block lives on and the ones it was carried into by `((block-ref))` alike; `references` names the latter, `--refs-limit` caps that list (0 lifts the cap) and `references_withheld` counts what was left out — with a range that includes occurrences outside it, so lifting the cap does not make the count zero. `--no-follow-refs` reports only where blocks live. `--due-from/--due-to` filter by `SCHEDULED`/`DEADLINE` instead. For a repeating task the next occurrence is derived (the date in its text moves on only when the task is ticked off by its checkbox in Logseq) and the range is applied to it; it is reported as `next_due`, and a repeater whose interval cannot be read is left out, counted in `repeating_excluded` and named on stderr. `--match` filters by what the task says (regex, case-insensitive, properties excluded). `--match` runs over the text as stored: `((refs))` are not resolved, and `^`/`$` anchor the whole text unless the pattern starts with `(?m)`. `--json` gives `{"todos": [...], "count": N}`, plus `repeating_excluded` when a due range left out repeaters it could not place; each task has `marker`, `content` (the text without marker, properties, `SCHEDULED`/`DEADLINE` and `LOGBOOK`), `page`, `uuid`, and `journal_day` when the page its block lives on is a journal. `scheduled`, `deadline`, `next_due` (all dates YYYY-MM-DD), `repeating`, `references` and `references_withheld` appear where they apply; a key that does not apply is absent, not null |
| `get-properties --page NAME [--property KEY]` | Get page properties, keyed as Logseq stores them (`due-date`, not the API's `dueDate`), with the original text alongside the parsed value |
| `doctor` | Health-check: Python, packages, connectivity, token, API, graph kind, graph, config, and whether writes are off (`read_only`). Exit 0 = ready to read |
| `init [--dry-run] [--force] [--output PATH]` | Write a config file suggested from your graph, with the counts each suggestion rests on. Writes to the config file in use (else the first search path), so a config in `~/.logseq-cli.toml` gets no second file; that file exists, so `--force` is needed, and it keeps `[safety]`. Allowed under `read_only` |

### Properties (3)

| Command | Description |
|---------|-------------|
| `set-property --page NAME --key KEY --value VAL [--dry-run]` | Set/update a page property, in the page's property block (made before the first block when there is none), so Logseq and `query-pages-by-property` see it at once. `title` is refused: it would rename the page; use `rename-page`. So is `collapsed`, which Logseq reads as the block's folded state. `--dry-run` shows the value being overwritten, or that the key is new. The key is stored as Logseq reads it back (lower-case, `_` as `-`, noted on stderr); a key Logseq would drop — whitespace, `/`, `:` and similar — is refused before anything is read, and so are `id` and `custom-id`, which Logseq reads as the block's uuid |
| `remove-property (--page NAME \| --id UUID) --key KEY [--dry-run]` | Remove a page property; the property block goes with its last one, unless it is the page's only block. `--id` targets a single block instead of the page. `--dry-run` names the value that would go, or reports that the key is not set. The key is addressed as `set-property` stores it |
| `set-block-property --id UUID --key KEY --value VAL [--dry-run]` | Set/update a block property. Fails on an unknown UUID, with or without `--dry-run`; `--dry-run` also shows the old value. Keys follow the `set-property` rule |

### Page Management (2)

| Command | Description |
|---------|-------------|
| `rename-page --page NAME --new-name NAME [--dry-run]` | Rename page (updates all references). `--dry-run` lists the pages whose `[[links]]` would be rewritten — the blast radius reaches the whole graph |
| `delete-page --page NAME [--force] [--ignore-refs] [--dry-run]` | Delete page. Prompts on a TTY; `--force` required non-interactively. Refuses while `((block-refs))` from other pages point into it; `--force` does not override that, `--ignore-refs` does |

### Query (1)

| Command | Description |
|---------|-------------|
| `query-pages-by-property --key KEY [--value VAL]` | Find pages by property value |

## Previews and refusals

Every command that writes takes `--dry-run`. What the previews show, and the
checks that refuse a write, command by command. With `read_only` on, a preview
is refused too (`reason: read_only`): one that says "would write" when the real
run cannot would lie about it.

```bash
# 1. --dry-run for the destructive commands (they cascade: children, source blocks)
logseq-cli remove-block --id "$UUID" --dry-run
#   [DRY RUN] Would remove block 6a76533e-...
#     descendants that would be removed too: 2
#     total blocks affected: 3
logseq-cli update-block --id "$UUID" --content "new text" --dry-run
logseq-cli copy-block --id "$UUID" --to-page "Target Page" --remove --dry-run
logseq-cli delete-page --page "Old Page" --dry-run

# 1b. --dry-run for the in-place writes too — they overwrite rather than cascade,
#     so the preview's job is to show the state that would be replaced.
logseq-cli set-todo-status --id "$UUID" --status DONE --dry-run
#   [DRY RUN] Would set status on block 6a76533e-...
#     marker: TODO -> DONE
logseq-cli set-property --page "Alice" --key team --value "Platform" --dry-run
#   [DRY RUN] Would set 'team::' on page 'Alice'
#     was: Core
#     now: Platform
logseq-cli remove-property --page "Alice" --key typo --dry-run
#   [DRY RUN] 'typo' is not set on page 'Alice'; nothing would be removed
logseq-cli set-block-property --id "$UUID" --key prio --value 3 --dry-run
logseq-cli add-block-ref --source-id "$UUID" --under-heading "## Tasks" --dry-run
logseq-cli add-note-content --page "Project Alpha" --content "Body" --dry-run
logseq-cli rename-page --page "Project Alpha" --new-name "Project Beta" --dry-run
#   [DRY RUN] Would rename page
#     from: Project Alpha
#     to:   Project Beta
#     pages with references that would be rewritten: 3

# 2. delete-page asks for --force when nothing can prompt; --json is not a yes
logseq-cli delete-page --page "Old Page" --json < /dev/null
#   {"error": "Refusing to delete page 'Old Page' non-interactively without --force. ..."}
#   non-zero exit

# 3. get-page separates "missing" from "empty"
logseq-cli get-page --page "Typo Page"     # (page does not exist) -> fails
logseq-cli get-page --page "Empty Page"    # (empty page)          -> exit 0

# 4. Bounded journal reads (see "Bounded output" below)
logseq-cli get-journal-range --from 2026-07-08 --to 2026-08-07 \
  --tail 7 --heading "## Log"
logseq-cli get-journal-summary --range "this week" --no-content

# 5. Errors go to stderr, never stdout; under --json mostly as a JSON object,
#    some as a plain "Error:" line, so rely on the exit status
logseq-cli get-properties --page "Missing Page" --json 2>err.json
```

## Bounding a read

What each option that bounds a read does. The measurements behind them are in
the [README](../README.md#bounded-output).

- `--tail N` / `--limit N` pick the newest / oldest N journal days. They are
  applied **before** fetching, so omitted days cost no API call. Mutually
  exclusive.
- `--heading "## Log"` returns only that section per day.
- `--no-content` (summary only) drops the bodies but keeps date, character
  count, topics and top concepts — enough for an overview, without the text.
- `--max-chars N` (`get-page`, `get-journal-range`) cuts the blocks so the
  output fits in N characters, measured in the format printed: a block in
  `--json` is several times its text. The cut falls between blocks in reading
  order, oldest day first; pages and days past it are not printed. `--json`
  puts `withheld` and `cut` (`before`, `section`, `section_uuid`, `later`,
  and `needs` when nothing fit) on
  the page or day the cut fell in. Only blocks are cut: page headers and
  backlinks always print, and when they alone exceed N the note says so.
- `--from-block UUID` continues a cut read: the same command plus the uuid
  the note names. Pages, days and blocks before it are skipped; its ancestors
  come along as context. Following the notes reads every block once: a
  repeated heading name, a heading rewritten by `--resolve-refs` or a section
  larger than N cannot send it back. A block that does not fit in N, alone or
  with its ancestors, ends the chain: the note names the `--max-chars` it
  needs, and `--json` puts it in `cut.needs`. A page named twice is refused,
  since its block uuids would be too.
- `get-page --outline` prints only the headings, each with its uuid,
  indented by how they nest (one tab per heading whose section holds it, not
  per `#`), and reads no backlinks. Read the outline of a large page first,
  then the section you need with `--heading`.

Search has the same shape: a word that recurs across months of notes matches
thousands of blocks, so `find-block --limit N` caps the output.

Truncation is never silent: whenever anything is omitted, a note goes to
**stderr** (`showing 3 of 20 journal day(s) ... 17 omitted`,
`showing 10 of 1382 match(es) ... 1372 omitted`, `12 block(s) withheld on
'...', cut in section '## Log' ... plus --from-block <uuid>`) while
stdout stays pure payload. Without truncation there is no note.

## Round-trip reduction

Seven changes focused on round-trip reduction and ergonomics. All read methods are cached in-memory for the duration of one process (60s TTL), and `get-journal-range` fetches in parallel.

```bash
# 1. Inline ((uuid)) refs while reading — no more N×get-block round-trips
logseq-cli get-page --page "2026-04-22, wednesday" --resolve-refs

# 2. UUID prefix per block line — replaces --json | jq pipelines
logseq-cli get-page --page "Project Alpha" --with-ids
# <uuid>\t<indent>\t<content>

# 3. get-todos shows page inline (plain-text); JSON unchanged
logseq-cli get-todos --status TODO
#   TODO [Project Alpha] Finish the tag support UI
#   DOING [2026-04-22, wednesday] Prepare the 1:1

# 3b. A task carried forward by ((block-ref)) is found on the day it stands,
# not only on the journal it was first written down in.
logseq-cli get-todos --from 2026-04-20 --to 2026-04-22
#   TODO [2026-03-04, wednesday] Write the migration guide
#       also on: 2026-04-22, wednesday; 2026-04-20, monday (+9 more)
# The task is one row: [page] is where the block lives, "also on" where it
# appears. --no-follow-refs reports only the former.

# 4. insert-block --tree: batch-insert a hierarchy in one call
logseq-cli insert-block --child-of "$UUID" --tree "### Meeting
	- Agenda
	- Outcome
		- Details"
# Or as JSON:
logseq-cli insert-block --page "Project" --top-level --tree \
  '[{"content":"### Section","children":[{"content":"Item"}]}]'

# 5. add-note-content --under-heading: heading-aware insertion for non-journal pages
logseq-cli add-note-content --page "Project" --under-heading "## Notes" \
  --content "- New observation\n\t- Details"
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
# Order is stable (sorted by date). Per-day errors embed an `error` field;
# the other days are still printed, and the call then fails, naming the days.
```

## Internationalization

`smart-query` supports both English and German keywords for template matching:

```bash
logseq-cli smart-query --request "offene aufgaben"   # German → finds open tasks
logseq-cli smart-query --request "open tasks"         # English → same result
```

The pattern for open tasks finds every open marker Logseq reads (the markers and their three states are in `CONTEXT.md`, under Task State); the pattern for done tasks finds `DONE` only.

Date formatting is locale-independent — weekday and month names are always English (as Logseq expects), regardless of system locale.

## Scripting Examples

See `examples/` directory:

- `backup-graph.sh` - Export all pages as a JSON backup
- `carry-todos-to-today.sh` - Carry open tasks older than N days into today's journal as `((block-refs))`; dry run unless `--write` (suitable for a morning cronjob)
- `carried-over-todos.sh` - Tasks standing in the last N days, longest-carried first (uses `references` to show how long each has been taken along)
- `daily-todos.sh` - Daily open-task overview (suitable for cronjob)
- `export-all-pages.sh` - Export all pages as individual JSON files
- `export-page.sh` - Export a page as Logseq-compatible markdown
- `morning-log.sh` - Add a timestamped log entry to today's journal
- `top-pages-pipeline.sh` - Pipeline with jq for graph statistics
- `weekly-todos.sh` - List all open TODOs, grouped by page
