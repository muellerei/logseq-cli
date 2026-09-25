# logseq-cli

[![tests](https://github.com/muellerei/logseq-cli/actions/workflows/tests.yml/badge.svg)](https://github.com/muellerei/logseq-cli/actions/workflows/tests.yml)
[![python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-blue)](https://github.com/muellerei/logseq-cli/actions/workflows/tests.yml)

Read and write a Logseq graph from a shell — pages, journals, blocks,
properties and graph analysis, without opening the app. It is built for a
caller that is a script or an AI agent rather than a person at a prompt:
`--json` on every command, payload on stdout, errors on stderr (mostly
as JSON), non-zero exit on failure, `--dry-run` on everything that writes, and output
bounded so it fits in a context window.

No vendor coupling — a plain Python package with `click` and `requests`.
See [AGENTS.md](AGENTS.md) for the workflows and gotchas, and the
[design notes](#design-notes) for the decisions behind the above.

An agent that has not met the tool yet finds it through the skill in
[`skills/logseq-cli/SKILL.md`](skills/logseq-cli/SKILL.md): when to reach for
it rather than the Markdown files, and the habits that prevent damage.
It follows the [Agent Skills](https://agentskills.io) format, so
`npx skills add muellerei/logseq-cli` installs it for most coding agents.

> **Which Logseq.** This is a tool for **file-based (Markdown) graphs**, driven
> over Logseq's local HTTP API — the 0.10.x line, tested against 0.10.15, and
> Logseq OG, which continues it. The DB version (2.x) keeps graphs in SQLite
> under a different data model and is **out of scope**; it answers the same API,
> so it connects and then reads empty. `logseq-cli doctor` names which one it
> found, so that is not something to work out by hand. Details under
> [Requirements](#requirements).

## Installation

### Requirements

| | |
|---|---|
| Python | 3.10 or newer (tested on 3.10-3.13 in CI) |
| `click` | >= 8.0 — command-line interface |
| `requests` | >= 2.28 — HTTP calls to Logseq |
| `tomli` | >= 2.0, installed only on Python 3.10; 3.11+ has `tomllib` built in |
| Logseq | Desktop app running, with the HTTP API server enabled |

Dependencies are installed for you by `pip`; nothing else is needed at runtime.
`pytest` comes with the `dev` extra and is only used for the test suite.

```bash
# Use it
pip install git+https://github.com/muellerei/logseq-cli.git

# Or work on it
git clone https://github.com/muellerei/logseq-cli.git
cd logseq-cli
pip install -e ".[dev]"
```

Developed and tested against Logseq Desktop 0.10.15 with a file-based
(Markdown) graph. Logseq split in 2026: the Markdown line continues as
Logseq OG (1.x) with an unchanged HTTP API, but this CLI is untested there.

The DB version (2.x) is **not supported**. It answers the same HTTP API, so
connecting to one succeeds — but it keeps the graph in SQLite under a
different data model, where `:block/original-name` and `:block/content` are
now `:block/title`. The fields these commands read are simply absent, so reads
come back empty rather than failing. `logseq-cli doctor` reports the graph
kind for exactly this reason, so an empty result does not have to be told
apart from an empty graph by hand.

## Quickstart

The CLI talks to Logseq's HTTP API, which is off by default. Two things to do
in the Logseq desktop app, both behind the **API** button in the toolbar:

1. **Start the server.** The menu shows the address it listens on —
   `http://127.0.0.1:12315` by default, which is what this CLI assumes. If the
   menu says *Stop server*, it is already running.
2. **Create a token** under *Authorization tokens*, and copy the value.

Then check the whole chain in one call:

```bash
logseq-cli --token "TOKEN" doctor
```

`doctor` tests each step separately — Python, packages, port, token, API,
graph kind, graph — and names the one that broke rather than leaving you to
guess. Exit 0 means everything is ready. Once it is:

```bash
export LOGSEQ_TOKEN="TOKEN"          # so you can drop --token from here on
logseq-cli get-all-pages | head
```

If that lists your pages, you are set. Everything below is detail.

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
| `LOGSEQ_CLI_CONFIG` | (none) | Path to a config file, overriding the default locations |

All connection settings can also be passed as CLI flags: `--host`, `--port`, `--token`.

### Config file

Most of the CLI needs no configuration. A few things cannot be guessed, because
they describe your graph rather than Logseq: which namespace holds your project
pages, which property marks a person page, what your journal sections are
called. Those live in an optional file:

```bash
# Suggest one from your own graph (counts included, writes nothing with --dry-run)
logseq-cli --token "TOKEN" init --dry-run
logseq-cli --token "TOKEN" init

# Or start from the commented example
cp config.example.toml ~/.config/logseq-cli/config.toml
```

A command that needs a setting you have not made says which one, and exits
non-zero rather than returning an empty result. See
[docs/configuration.md](docs/configuration.md) for every option, what happens
without it, and how to read the right values out of your own graph.

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
| `get-page --page NAME [--no-backlinks] [--resolve-refs] [--with-ids] [--heading "## X"] [--outline] [--max-chars N] [--from-block UUID] [--format markdown]` | Page content with backlinks; optionally inline `((uuid))` refs or prefix UUIDs per line. `--no-backlinks` skips the backlink lookup, `--heading` returns only that section (searched recursively). `--outline` lists the headings with their uuids; `--max-chars` cuts the output to size and `--from-block` continues it — see [Bounded output](#bounded-output). With `--resolve-refs`, a ref whose target was deleted is named on stderr — on stdout it renders exactly like an unresolved one |
| `get-block --id UUID [--no-children]` | Block by UUID; `--no-children` returns the block alone |
| `find-block --content TEXT [--page NAME] [--regex] [--first \| --limit N \| --exactly-one] [--with-children \| --uuid-only]` | Find blocks by content. `--uuid-only` prints bare uuids, one per line, and fails on no match. `--exactly-one` fails unless exactly one block matches and lists the matches otherwise; use it for a block to write to, `U=$(... --exactly-one --uuid-only)`, where `--first` would pick one of several. A common word matches thousands of blocks, so `--limit N` caps the output and the number withheld goes to stderr; `--first` is the same with N=1. `--with-children` prints each match with its sub-blocks indented, instead of guessing a line count with `get-page \| grep -A<n>`; costs one extra read per match, capped at 25 with the remainder reported |
| `get-journal-range --from DATE --to DATE [--resolve-refs] [--tail N] [--limit N] [--heading "## Log"] [--max-chars N] [--from-block UUID]` | Batch journal read; parallel (5 workers default). `--tail/--limit/--heading/--max-chars` bound the output, `--from-block` continues a cut one — see [Bounded output](#bounded-output) |
| `search-pages --query TEXT` | Case-insensitive name search |
| `get-backlinks --page NAME [--with-context] [--limit N]` | Pages linking to NAME. `--with-context` also shows the blocks that do the linking — they arrive with the same API call, so it costs no extra read; `--limit` (default 3) caps the blocks per page and reports the remainder; `0` keeps all |
| `get-journal-summary --range RANGE [--no-content]` | Journal summary (today, this week, last 30 days). `--no-content` drops the per-day bodies |
| `analyze-graph [--days N]` | Graph structure analysis. `--days N` adds the pages modified in the last N days |
| `find-knowledge-gaps [--min-refs N] [--include-orphans/--no-include-orphans]` | Missing/underdeveloped/orphaned pages. `--min-refs` (default 2) is how many incoming references a short page needs before it counts as underdeveloped rather than unused |
| `analyze-journal-patterns [--timeframe RANGE] [--mood/--no-mood] [--topics/--no-topics]` | Journal entry patterns over `--timeframe` (default "last 30 days"). `--no-mood` and `--no-topics` drop those sections |
| `smart-query --request TEXT [--advanced] [--include-query]` | Datalog queries (natural language, or `--advanced` to pass raw Datalog through). `--include-query` prints the generated query alongside the result |
| `suggest-connections [--min-confidence N] [--min-shared N] [--max-suggestions N] [--focus PAGE]` | Topic-based connection suggestions. `--min-shared` (default 3) is the real filter — it sets how many topics two pages must share before the pair counts at all; `--min-confidence` (default 0.3) then scores it. `--max-suggestions` (default 10) caps the list and the remainder is reported as withheld; `total_found` counts what the graph held, not what survived the cap. `--focus` restricts to one page |
| `get-page-stats --page NAME` | Page statistics (blocks, words, in/outbound links) |

### Write (5)

| Command | Description |
|---------|-------------|
| `create-page --name NAME [--content TEXT] [--dry-run]` | Create a new page. Fails if it already exists, rather than appending `--content` to what is there; `--dry-run` reports which of the two a run would be. An `id::` line in `--content` is dropped with a note |
| `add-journal-entry --content TEXT [--date DATE] [--multi-block] [--dry-run]` | Add journal entry (deprecated, use add-journal-block). `--date` defaults to today; `--multi-block` splits multi-line content into one block per line. An `id::` line in `--content` is dropped with a note |
| `add-journal-block --content TEXT [--date DATE] [--upsert-heading "### X"] [--no-preserve] [--keep-ids]` | Add block to journal — auto-detects hierarchical content (`--under-heading`, `--top-level`, `--dry-run`). `--date` defaults to today. `--upsert-heading` updates a matching child block under `--under-heading` instead of adding a second one; the block keeps its properties, as with `update-block`. `--content-file FILE` reads the whole file as one tree: no shell quoting, flush `- ` lines become sibling roots; `--content-file -` reads stdin. On `update-block`, `insert-block`, `add-note-content` and `add-journal-content`, `--content-file` is `--content` read from a file, with the same rules |
| `add-journal-content (--content TEXT \| --content-file FILE) [--date DATE] [--keep-ids]` | Add hierarchical content to journal (`--under-heading`, `--top-level`, `--dry-run`). `--date` defaults to today |
| `add-note-content --page NAME (--content TEXT \| --content-file FILE) [--under-heading "## X"] [--no-create] [--property K=V] [--keep-ids] [--dry-run]` | Add content to any page; optionally under a heading (created if missing). The page is created when missing unless `--no-create` is given. `--property` sets `key:: value` on the root block, repeatable. `--dry-run` reports the target, the block count and whether page or heading would be created |

### Edit (8)

| Command | Description |
|---------|-------------|
| `update-block (--id UUID \| --where-content TEXT [--page NAME] [--regex]) (--content TEXT \| --content-file FILE) [--dry-run]` | Update block content. `--content` is ONE block and has no tree path: a `- ` or `# ` line after the first (indented too) or a code fence nothing closes is refused, since Logseq would read it as a block of its own: use `insert-block --child-of` for children. `--where-content` selects by text instead of UUID and aborts unless exactly one block matches. The block's properties are kept as written, values as their original text, unless `--content` sets the key itself; the one change is Logseq's own spelling of a key (`created_at` is stored and written back as `created-at`). The block's own `id::` line passes; one naming another uuid is dropped with a note, since it would become this block's uuid |
| `remove-block --id UUID [--ignore-refs] [--dry-run]` | Delete a block and its children (alias: `delete-block`). `--dry-run` reports the descendant count. Refuses while `((block-refs))` from elsewhere point into the block or its children, and lists them; `--ignore-refs` removes anyway |
| `add-block-ref --source-id UUID (--journal-date DATE \| --page NAME) [--under-heading "## X"] [--dry-run]` | Write a `((block-ref))` pointing at an existing block. Journal defaults to today, heading to `LOGSEQ_JOURNAL_HEADING`. A source uuid no block has is refused before anything is written, `--dry-run` included — a ref to it would render as nothing |
| `set-todo-status (--id UUID \| --content TEXT --page NAME) --status DONE [--follow-refs] [--dry-run]` | Swap a TODO/DOING/DONE marker without retyping the line. `--follow-refs` updates the original when the block is just a `((ref))`. Ambiguous `--content` aborts and lists candidates. `--dry-run` shows the old and new marker |
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
| `get-todos [--page NAME] [--status S] [--tag TAG] [--match REGEX] [--from DATE] [--to DATE] [--due-from DATE] [--due-to DATE] [--include-done] [--refs-limit N] [--no-follow-refs]` | List tasks (page name shown inline in plain-text output). `--from/--to` date a task by every journal it stands in, the page its block lives on and the ones it was carried into by `((block-ref))` alike; `references` names the latter, `--refs-limit` caps that list (0 lifts the cap) and `references_withheld` counts what was left out — with a range that includes occurrences outside it, so lifting the cap does not make the count zero. `--no-follow-refs` reports only where blocks live. `--due-from/--due-to` filter by `SCHEDULED`/`DEADLINE` instead. For a repeating task the next occurrence is derived (the date in its text moves on only when the task is ticked off by its checkbox in Logseq) and the range is applied to it; it is reported as `next_due`, and a repeater whose interval cannot be read is left out, counted in `repeating_excluded` and named on stderr. `--match` filters by what the task says (regex, case-insensitive, properties excluded). `--match` runs over the text as stored: `((refs))` are not resolved, and `^`/`$` anchor the whole text unless the pattern starts with `(?m)`. `--json` gives `{"todos": [...], "count": N}`, plus `repeating_excluded` when a due range left out repeaters it could not place; each task has `marker`, `content` (the text without marker, properties, `SCHEDULED`/`DEADLINE` and `LOGBOOK`), `page`, `uuid`, and `journal_day` when the page its block lives on is a journal. `scheduled`, `deadline`, `next_due` (all dates YYYY-MM-DD), `repeating`, `references` and `references_withheld` appear where they apply; a key that does not apply is absent, not null |
| `get-properties --page NAME [--property KEY]` | Get page properties, keyed as Logseq stores them (`due-date`, not the API's `dueDate`), with the original text alongside the parsed value |
| `doctor` | Health-check: Python, packages, connectivity, token, API, graph kind, graph, config. Exit 0 = ready |
| `init [--dry-run] [--force] [--output PATH]` | Write a config file suggested from your graph, with the counts each suggestion rests on |

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

## Safety and output size

Safety and output-size work from an audit against common CLI conventions
(clig.dev, POSIX/grep, agent tool-design guidance). **Defaults are
unchanged** — without the new flags every command behaves exactly as before.

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

# 2. delete-page: --json is no longer an implicit --force
logseq-cli delete-page --page "Old Page" --json < /dev/null
#   {"error": "Refusing to delete page 'Old Page' non-interactively without --force. ..."}
#   non-zero exit — the output format no longer doubles as a confirmation.

# 3. get-page separates "missing" from "empty"
logseq-cli get-page --page "Typo Page"     # (page does not exist) -> fails
logseq-cli get-page --page "Empty Page"    # (empty page)          -> exit 0

# 4. Bounded journal reads (see "Bounded output" below)
logseq-cli get-journal-range --from 2026-07-08 --to 2026-08-07 \
  --tail 7 --heading "## Log"
logseq-cli get-journal-summary --range "this week" --no-content

# 5. delete-block works as an alias for remove-block
logseq-cli delete-block --id "$UUID" --dry-run

# 6. Errors go to stderr, never stdout; under --json mostly as a JSON object,
#    some as a plain "Error:" line, so rely on the exit status
logseq-cli get-properties --page "Missing Page" --json 2>err.json
```

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
# Order is stable (sorted by date). Per-day errors embed an `error` field
# and the range continues.
```

## Bounded output

Reads grow with the graph. On a real one (four years of daily entries) the
unbounded commands produce far more text than an LLM agent can hold:

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

## Design notes

The decisions that shaped the tool more than any feature did. Most came out of
a defect; the [CHANGELOG](CHANGELOG.md) carries the full account of what was
wrong, how it was found and what the fix cost. The last three are about what the
tool deliberately does not do — two mechanisms not built, one the edge of what
it is for.

They are all the same rule applied in different places: **an answer must not
have two possible causes.** An empty list has to mean "nothing matched" and
never "you did not configure which property to look at", which is why no
graph-specific setting has a built-in default — a command that needs one says
so and exits non-zero instead of returning an empty result that reads like an
answer. The notes below are that rule meeting the places where Logseq's API
makes it hard.

### Writes are verified, not assumed

Logseq answers a failed write with HTTP 200 and a `null` body, so a command
that trusts the status code reports "Added N block(s)" over a journal entry
that was never written — and for a journal entry, nothing else will ever tell
you. Every insert path now proves the write by reading the block back, which
costs a round trip per write and is worth it: `copy-block --remove` used to
delete the source against a copy that had not landed. The strict path is the
default; tolerating partial writes is something a caller now has to ask for.

`move-block` is the clearest case. `moveBlock` answers `null` for a move that
worked, for a target that does not exist, and for one Logseq refuses — it
declines to move a block into its own subtree and says so only by doing
nothing. So the move is proven by re-reading, and the obvious check is not
enough: for `--before`, "same parent" would also hold for a move that did
nothing at all, since source and target usually share one already, so the
sibling order is what gets compared. That order has to come from the page tree
when the target sits at the top level, because its parent is then the page and
`getBlock` does not answer for a page; up to 0.13.0 every top-level `--before`
was reported as failed, moved or not. The subtree case is checked before the
call instead, so it is refused with its reason rather than guessed at
afterwards. The option names mislead as well, which
only a live graph will tell you: `before: true` inserts a sibling in front, and
everything else — including the `sibling: true` the API's own option list
suggests — nests the block as the target's first child. This matters beyond
tidiness, because the alternative route quietly destroys data: `copy-block
--remove` writes a new block with a new UUID and deletes the original, so every
`((block-ref))` aimed at it dangles afterwards. A structural move keeps the
UUID and the references with it.
See [0.6.0](CHANGELOG.md#060---2026-08-07) and [0.8.0](CHANGELOG.md#080---2026-08-28).

### Query values are escaped in one place

Values entering datalog queries were interpolated with f-strings: one call site
escaped quotes but not backslashes, the rest escaped nothing, so a page name
could alter the query around it. The fix was a build layer
([`datalog.py`](logseq_cli/datalog.py)) that every interpolating call site goes
through, rather than a patch at each site — scattered escaping is the kind of
thing that holds until the next call site is added and nobody remembers the
rule. `smart-query --advanced` stays a raw pass-through, because a documented
escape hatch is safer than one people invent for themselves.
See [0.9.0, Security](CHANGELOG.md#090---2026-09-14).

### Analysis output is measured against a real graph

`analyze-graph` reported 438 open tasks for a graph with 256, because it
counted "todo" anywhere in any casing; the mood counters scored "nicht
zufrieden" as positive, 16% of positive hits in a 90-day sample; and
`find-knowledge-gaps` reported 596 orphans that were mostly Logseq's own
by-products. None of that was caught by tests asserting that output exists —
it took reading the numbers next to a graph whose real answer was known. A
plausible number that gets believed is worse than an obvious failure, so these
commands now measure one defined thing each and agree with one another.
See [0.9.0, Fixed](CHANGELOG.md#090---2026-09-14).

### Output is bounded because the consumer has a context limit

An agent reading a month of journals gets 431,996 characters, against a tool
response cap of roughly 25,000 tokens — the call does not fail, it truncates
somewhere and the agent reasons on a fragment without knowing it. So `--tail`
and `--limit` filter **before** fetching rather than after, which keeps the
omitted days from costing API calls as well. Truncation is never silent: the
count of omitted days goes to stderr while stdout stays pure payload.
See [0.6.0](CHANGELOG.md#060---2026-08-07).

### A block reference is not readable on its own

Logseq stores a quoted block as `((uuid))`, which is enough for the app to
render the original but tells a reader nothing at all. A page full of them
arrives as a page full of holes, and the caller has to spend one `get-block`
per hole to find out what it said.

`--resolve-refs` on `get-page` and `get-journal-range` replaces each reference
with the text it points at, followed by the page it came from
(`the actual text ↳ Meeting Notes`), and descends into child blocks so a nested
quote resolves too. A reference whose target cannot be read is left as
`((uuid))` rather than dropped or blanked: a hole you can see beats a sentence
that silently lost a clause.

Without the flag both commands count what is left and say so on stderr —
`3 unresolved block-ref(s) in output` — because the output otherwise looks
complete and is not. `get-page` was silent about this until the count was added
there too; the same page read through two commands had given two different
answers about whether it was whole.

### A task is where it stands, not only where it was written

A todo block exists once. Carrying it forward into later journals is done with
a `((block-ref))`, and that reference is not a copy — it is the same block in a
second place, which is why checking off the reference checks off the original.
A tool that finds tasks through `:block/page` alone therefore sees only the day
a task was first written down, and a query for this week returns nothing about
the tasks that actually stood in it. The failure is quiet: an empty task list
looks like an empty week.

Logseq's own `(between ...)` filter reads the same way, which is how the
problem arrives in the forum rather than in a bug tracker — *"the tasks are not
in the journal pages and the between query only looks at the journal page
dates"*
([discuss.logseq.com](https://discuss.logseq.com/t/creating-a-query-for-overdue-tasks/12408)).
The advanced-query answer given there reaches for `:block/refs`, one block
reference at a time.

So `get-todos` follows that relation by default rather than behind a flag: a
default that answers incompletely is worse than one that costs a read, because
the caller has no way to tell the two apart. The task stays one row — `page`
and `uuid` keep naming the original block, `references` names the days it was
carried into. `--refs-limit` caps that list and `references_withheld` counts
the rest, because a task carried 33 times must not decide the size of the
output, and `--no-follow-refs` restores the older reading for callers who want
to know where blocks live rather than where they appear.

`references_withheld` counts two things a range query leaves out: occurrences
beyond the cap, and occurrences outside the range itself. Lifting the cap with
`--refs-limit 0` therefore does not drive the count to zero — a task carried
since March still reports the days before the queried week. That is the reading
a range query wants, because the alternative is a task that looks new.

### Exit status: done or not done, and no resume

A command exits `0` when it did what it says, and non-zero when it did not;
the error on stderr says why, under `--json` mostly as an object. The number
itself carries no meaning. Some errors end with 1 and some with 2, but
nothing promises which, so do not branch on it.

This used to be less honest. The documentation promised, in different places,
that there was no second exit code and that 2 meant "refused, fix the call";
the code kept neither. Separating a refused call from a failed one by its exit
status would be possible, but nothing showed that a caller needed it: in a
month of agent sessions with this tool, agents read the error text and
corrected their calls, and most calls ran through a pipe, where the status
never reached them. So the number stays without a meaning for now, which also
means it can gain one later without breaking a caller.
[ADR 0004](docs/adr/0004-a-non-zero-exit-means-not-done.md) records the
decision and when to revisit it.

What the rule does demand is that `0` is never a lie: a call that exits `0`
without having done what it says, such as a replacement that did not reach
the graph or a read that failed and came back empty, is a bug
([#93](https://github.com/muellerei/logseq-cli/issues/93) collected the ones
found). Where part of the answer was already read, it still appears on
stdout, followed by the error. A non-zero status is not on its own a reason
to retry: a missing UUID fails identically on the second attempt.

The harder question is what happens when a multi-block write dies halfway.
`insertBatchBlock` is not atomic — verified against a live graph, a malformed
node is skipped while its siblings land — and the API offers no rollback, so
the blocks that made it stay. Other tools solve this with a durable operation
record and a `--resume` flag. This one does not, and the reason is a count: over
the recorded history of this tool, 353 real write invocations produced **zero**
partial writes. The two partial-write bugs in the CHANGELOG were defects in the
counting, found while testing, not aborts in use. A resume path would mean
durable local state, an idempotency story for every one of the write commands,
and a check that the graph has not moved underneath — a large mechanism for an
event that has not yet happened. Instead the abort names the damage: how many
blocks are already in the graph, that there is no rollback, and that retrying
the same input will duplicate them. If the count ever stops being zero, that is
the signal to build the mechanism, and the measurement is cheap to repeat.

### A write takes its target by name, not from a pipe

Every command that writes to the graph is told where — a uuid, a page name, a
date, or text that must match exactly one block — one target per call. Content
may come from stdin (`--content-file -`); a list of targets never does. Bulk
work is a shell loop over explicit ids, and that is deliberate. A loop gives
every write its own checks and its own exit status. A pipe into `remove-block`
would carry one `--ignore-refs` for every block in it — the blanket override
the ref check is built to refuse, which is why not even `--force` switches it
off. And a preview has to be of the call that runs: whatever produces a list of
targets reads the graph, and stdin can be read once, so dry-running a pipe
means running its producer twice. If the graph moves in between, the preview
showed other uuids than the ones then written. With the ids on the command
line, `--dry-run` and the write take the same arguments.

The use does not ask for it. Agent sessions working with the tool loop over
explicit ids, some of them around a writer, and carry values from one call to
the next in shell variables; none has read another call's output on its stdin.
If that changes, it is the signal to design a batch mode, with the preview,
partial failures and the exit status worked out before any command reads its
targets from stdin.

### This is a tool for file-based graphs

Logseq split in 2026. The Markdown line continues as
[Logseq OG](https://github.com/logseq/og); the DB version (2.x) keeps the name
and the roadmap, and stores graphs in SQLite.

This tool is for the Markdown side, by preference and not by accident. Notes
kept as plain text on disk can be read by a dozen other programs, versioned in
git, and grepped without asking anything for permission; driving them from a
shell is the natural extension of that, not a workaround. A database buys other
things — speed on large graphs, richer properties — and that is a fair trade for
those who want it; it is simply not the trade these notes are kept under.
Logseq OG is where file-based graphs live now, and this follows them there.

Scope is the decision; the cost only says how firm it is. Of the fourteen
datalog attributes used here, seven survive into the
[DB schema](https://github.com/logseq/logseq/blob/master/deps/db/src/logseq/db/frontend/schema.cljs)
and seven do not: `content`, `original-name`, `properties`, `marker`,
`deadline`, `scheduled` and `journal` have no counterpart, which is 46 of 100
attribute uses. `:block/content` becoming `:block/title` is the easy half. The
rest are model changes — properties are entities rather than a map inside a
block, task markers give way to a `Status` property whose values are themselves
entities, and blocks and pages are unified as nodes without `((uuid))`
references. Every property command and the whole todo surface would be
rewritten, not ported. Two data models under one set of commands is how a tool
gets a dialect problem, and neither side ends up well served.

None of this rules out a DB version later, as its own project or as a port.
It would be a different tool, and it should be built as one.

Until then `doctor` names the graph kind, because the failure is quiet: a 2.x
graph answers the same API on the same port, so it connects, and then returns
empty results that read exactly like an empty graph. Being told which Logseq is
on the other end beats inferring it from nothing.

## Internationalization

`smart-query` supports both English and German keywords for template matching:

```bash
logseq-cli smart-query --request "offene aufgaben"   # German → finds open tasks
logseq-cli smart-query --request "open tasks"         # English → same result
```

Date formatting is locale-independent — weekday and month names are always English (as Logseq expects), regardless of system locale.

## Scripting Examples

See `examples/` directory:

- `backup-graph.sh` - Export all pages as a JSON backup
- `carried-over-todos.sh` - Tasks standing in the last N days, longest-carried first (uses `references` to show how long each has been taken along)
- `daily-todos.sh` - Daily TODO overview (suitable for cronjob)
- `export-all-pages.sh` - Export all pages as individual JSON files
- `export-page.sh` - Export a page as Logseq-compatible markdown
- `morning-log.sh` - Add a timestamped log entry to today's journal
- `top-pages-pipeline.sh` - Pipeline with jq for graph statistics
- `weekly-todos.sh` - List all open TODOs, grouped by page

## Architecture

```
logseq-cli/
├── logseq_cli/
│   ├── api.py          # HTTP API client (requests.post against Logseq)
│   ├── blockprops.py   # Property keys and values: what Logseq reads back, what a write keeps
│   ├── blocktext.py    # How Logseq reads a block's lines: code blocks, block boundaries
│   ├── cliinput.py     # --content, --content-file and --tree, taken from the command line
│   ├── config.py       # Config file discovery, loading and lookup
│   ├── datalog.py      # EDN/datalog query building (value quoting, keywords)
│   ├── dates.py        # Date keywords, journal days, repeaters, journal title formats
│   ├── group.py        # The click group: global options, API client
│   ├── headings.py     # Compare, find and add a heading on a page
│   ├── ids.py          # id:: lines in written text: dropped with a note, or kept by --keep-ids
│   ├── lookup.py       # Blocks by content, backlinks, incoming block refs, page text
│   ├── outlinetext.py  # Indented outline text to a block tree, and back
│   ├── output.py       # Results on stdout, failures on stderr, --json
│   ├── pagenames.py    # Which page a name means: an alias as Logseq resolves it
│   ├── render.py       # Blocks to text; finding and resolving references
│   ├── strictinsert.py # Strict Insert: writes checked to land where asked, moves included
│   ├── commands/       # One module per group of commands
│   │   ├── pages.py        # create/get/search/rename/delete a page
│   │   ├── blocks.py       # read a block, find blocks
│   │   ├── edit.py         # write, move, copy and remove blocks
│   │   ├── journal.py      # journal entries and ranges
│   │   ├── todos.py        # TODO markers and their references
│   │   ├── properties.py   # page and block properties
│   │   ├── analysis.py     # graph-wide analysis and suggestions
│   │   ├── query.py        # smart-query
│   │   └── meta.py         # init and doctor
│   └── cli.py          # Entry point: imports every command module
├── examples/         # Shell scripts for scripting/cronjobs
├── skills/logseq-cli/SKILL.md  # The skill agents find the tool by
└── pyproject.toml
```

A command exists once its module has been imported, and `cli.py` is the file
that imports them. `docs/adr/0001-explicit-command-registration.md` says why
that list is written out rather than discovered by scanning the directory.

The CLI communicates with Logseq's built-in HTTP API (Fastify server on port 12315).
The core commands are inspired by [joelhooks/logseq-mcp-tools](https://github.com/joelhooks/logseq-mcp-tools), extended with property management, page operations, and property-based queries.
