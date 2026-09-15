# logseq-cli

[![tests](https://github.com/muellerei/logseq-cli/actions/workflows/tests.yml/badge.svg)](https://github.com/muellerei/logseq-cli/actions/workflows/tests.yml)

Read and write a Logseq graph from a shell — pages, journals, blocks,
properties and graph analysis, without opening the app. It is built for a
caller that is a script or an AI agent rather than a person at a prompt:
`--json` on every command, payload on stdout, errors as JSON on stderr,
non-zero exit on failure, `--dry-run` on everything that writes, and output
bounded so it fits in a context window.

No vendor coupling — a plain Python package with `click` and `requests`.
See [AGENTS.md](AGENTS.md) for the workflows and gotchas, and the
[design notes](#design-notes) for the decisions behind the above.

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

### Read (14)

| Command | Description |
|---------|-------------|
| `get-all-pages` | List all pages |
| `get-page --page NAME [--resolve-refs] [--with-ids] [--format markdown]` | Page content with backlinks; optionally inline `((uuid))` refs or prefix UUIDs per line |
| `get-block --id UUID` | Block by UUID |
| `find-block --content TEXT [--page NAME] [--regex] [--first \| --limit N] [--with-children]` | Find blocks by content. A common word matches thousands of blocks, so `--limit N` caps the output and the number withheld goes to stderr; `--first` is the same with N=1. `--with-children` prints each match with its sub-blocks indented, instead of guessing a line count with `get-page \| grep -A<n>`; costs one extra read per match, capped at 25 with the remainder reported |
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
| `create-page --name NAME [--content TEXT] [--dry-run]` | Create a new page. Fails if it already exists, rather than appending `--content` to what is there; `--dry-run` reports which of the two a run would be |
| `add-journal-entry --content TEXT [--dry-run]` | Add journal entry (deprecated, use add-journal-block) |
| `add-journal-block --content TEXT` | Add block to journal — auto-detects hierarchical content (`--under-heading`, `--dry-run`). `--content-file FILE` reads the whole file as one tree: no shell quoting, flush `- ` lines become sibling roots |
| `add-journal-content --content TEXT` | Add hierarchical content to journal (`--under-heading`, `--dry-run`) |
| `add-note-content --page NAME --content TEXT [--under-heading "## X"] [--dry-run]` | Add content to any page; optionally under a heading (created if missing). `--dry-run` reports the target, the block count and whether page or heading would be created |

### Edit (11)

| Command | Description |
|---------|-------------|
| `update-block (--id UUID \| --where-content TEXT [--page NAME] [--regex]) --content TEXT [--dry-run]` | Update block content. `--content` is ONE block and has no tree path: newline bullets are rejected, indented ones too: use `insert-block --child-of` for children. `--where-content` selects by text instead of UUID and aborts unless exactly one block matches |
| `remove-block --id UUID [--dry-run]` | Delete a block and its children (alias: `delete-block`). `--dry-run` reports the descendant count |
| `add-block-ref --source-id UUID (--journal-date DATE \| --page NAME) [--under-heading "## X"] [--dry-run]` | Write a `((block-ref))` pointing at an existing block. Journal defaults to today, heading to `LOGSEQ_JOURNAL_HEADING`. `--dry-run` also verifies the source block exists — a ref to a missing UUID renders as nothing |
| `set-todo-status (--id UUID \| --content TEXT --page NAME) --status DONE [--follow-refs] [--dry-run]` | Swap a TODO/DOING/DONE marker without retyping the line. `--follow-refs` updates the original when the block is just a `((ref))`. Ambiguous `--content` aborts and lists candidates. `--dry-run` shows the old and new marker |
| `replace-text --page NAME --find TEXT --replace TEXT` | Search & replace with regex and dry-run support |
| `insert-block --content TEXT [--child-of UUID]` | Insert block at position (after/before/child-of/page) |
| `insert-block --tree "<tab-or-json>" [--quiet]` | `--quiet` prints only the confirmation line, not one uuid line per block |
| `insert-block --child-of UUID --first` | Insert as FIRST child instead of appending last (works with `--content` and `--tree`; order preserved). Only valid with `--child-of` |
| `insert-block --tree "<tab-or-json>" [--child-of UUID \| --page NAME --top-level]` | Batch-insert a hierarchy in one call (DFS pre-order UUIDs returned). `--tree-file FILE` reads the same tab-indented text or JSON from a file |
| `insert-block --tree ... --keep-ids` | Keep the `id::` values in the tree instead of letting Logseq mint new ones, for moving or restoring an outline. Without it they are dropped and the count is reported on stderr, because a copy whose original still exists would otherwise put one uuid on two blocks |
| `copy-block --id UUID --to-page NAME [--remove] [--dry-run]` | Copy/move block with children to another page |
| `move-block --id UUID (--under UUID \| --before UUID) [--dry-run]` | Structural move: the block keeps its UUID, so `((block-refs))` to it survive. Prefer over `copy-block --remove`, which writes a new block and deletes the original. `--under` nests as first child, `--before` places it in front as a sibling |

### Meta (4)

| Command | Description |
|---------|-------------|
| `get-todos [--page NAME] [--status S] [--tag TAG]` | List tasks (page name shown inline in plain-text output) |
| `get-properties --page NAME [--property KEY]` | Get page properties |
| `doctor` | Health-check: Python, packages, connectivity, token, API, graph kind, graph, config. Exit 0 = ready |
| `init [--dry-run] [--force] [--output PATH]` | Write a config file suggested from your graph, with the counts each suggestion rests on |

### Properties (3)

| Command | Description |
|---------|-------------|
| `set-property --page NAME --key KEY --value VAL [--dry-run]` | Set/update a page property. `--dry-run` shows the value being overwritten, or that the key is new |
| `remove-property (--page NAME \| --id UUID) --key KEY [--dry-run]` | Remove a page property. `--id` targets a single block instead of the page. `--dry-run` names the value that would go, or reports that the key is not set |
| `set-block-property --id UUID --key KEY --value VAL [--dry-run]` | Set/update a block property. `--dry-run` shows the old value and fails on an unknown UUID, which the write path cannot detect |

### Page Management (2)

| Command | Description |
|---------|-------------|
| `rename-page --page NAME --new-name NAME [--dry-run]` | Rename page (updates all references). `--dry-run` lists the pages whose `[[links]]` would be rewritten — the blast radius reaches the whole graph |
| `delete-page --page NAME [--force] [--dry-run]` | Delete page. Prompts on a TTY; `--force` required non-interactively |

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
#   exit 1 — the output format no longer doubles as a confirmation.

# 3. get-page separates "missing" from "empty"
logseq-cli get-page --page "Typo Page"     # (page does not exist) -> exit 1
logseq-cli get-page --page "Empty Page"    # (empty page)          -> exit 0

# 4. Bounded journal reads (see "Bounded output" below)
logseq-cli get-journal-range --from 2026-07-08 --to 2026-08-07 \
  --tail 7 --heading "## Log"
logseq-cli get-journal-summary --range "this week" --no-content

# 5. delete-block works as an alias for remove-block
logseq-cli delete-block --id "$UUID" --dry-run

# 6. Errors are JSON when --json is set — always on stderr, never on stdout
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

Search has the same shape: a word that recurs across months of notes matches
thousands of blocks, so `find-block --limit N` caps the output.

Truncation is never silent: whenever anything is omitted, a note goes to
**stderr** (`showing 3 of 20 journal day(s) ... 17 omitted`,
`showing 10 of 1382 match(es) ... 1372 omitted`) while stdout stays pure
payload. Without truncation there is no note.

## Design notes

Seven decisions that shaped the tool more than any feature did. Most came out of
a defect; the [CHANGELOG](CHANGELOG.md) carries the full account of what was
wrong, how it was found and what the fix cost. The last two are about what the
tool deliberately does not do — one a mechanism not built, one the edge of what
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
sibling order is what gets compared. The option names mislead as well, which
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

### Failure has one exit code, and no resume

A command exits `0` when it did what it said, and non-zero when it did not.
There is deliberately no second exit code separating "your input was wrong" from
"the operation failed": with `--json`, the error object already carries the
reason as text, and a numeric code repeating that classification is a second
view that can drift away from the first. Callers that need to distinguish the
cases read the JSON on stderr; callers that only need to know whether to stop
read the exit status. A non-zero status is not on its own a reason to retry — a
missing UUID fails identically on the second attempt.

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
│   ├── api.py       # HTTP API client (requests.post against Logseq)
│   ├── datalog.py   # EDN/datalog query building (value quoting, keywords)
│   ├── helpers.py   # Date parsing, block processing, backlink search
│   └── cli.py       # Click CLI with all commands
├── examples/        # Shell scripts for scripting/cronjobs
└── pyproject.toml
```

The CLI communicates with Logseq's built-in HTTP API (Fastify server on port 12315).
The core commands are inspired by [joelhooks/logseq-mcp-tools](https://github.com/joelhooks/logseq-mcp-tools), extended with property management, page operations, and property-based queries.
