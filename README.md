# logseq-cli

[![tests](https://github.com/muellerei/logseq-cli/actions/workflows/tests.yml/badge.svg)](https://github.com/muellerei/logseq-cli/actions/workflows/tests.yml)
[![python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-blue)](https://github.com/muellerei/logseq-cli/actions/workflows/tests.yml)

Let your AI agent write into your Logseq graph without breaking it.

Left to edit the Markdown files, an agent has to guess where an entry belongs
and how it should look, and you correct it when it guesses wrong. With
logseq-cli it asks Logseq instead: the tool works through the running Logseq
app, so entries land in the right place and in Logseq's own format, and your
notes stay plain Markdown files. Reads come back filtered, and the large ones
cut to a size the agent asks for, which saves it tokens.

You can keep typing in Logseq while your agent writes. Blocks it adds do not
take your cursor, and a write that would change the block you are typing in is
refused. Every write is checked: logseq-cli confirms with Logseq that it
landed before it reports success, and it never overwrites the block you are
typing in. [How that works](#writes-are-verified-not-assumed).

```text
   You, in Logseq                          Your agent, through logseq-cli
   ──────────────────────────────────────  ──────────────────────────────────
1  typing "Call with Alex about the con|"  adds "**14:31** Decided to ship …"
                                           → lands under ## Log, proven
                                             written; your cursor stays put
2  still typing "…the contract|"           changes the block you are in
                                           → refused; nothing you typed
                                             is lost
3  your journal afterwards:
   ## Log
   - Call with Alex about the contract, ok
   - **14:31** Decided to ship on Friday
```

Your agent needs to be one that can run commands on your computer, such as
Claude Code, Codex or Cursor; it calls logseq-cli like any other command, with
no MCP server or Logseq plugin to install. logseq-cli itself sends your notes nowhere but
to Logseq, on your machine by default.

> **Which Logseq.** For **Markdown graphs**: Logseq 0.10.x (tested against
> 0.10.15) and Logseq OG, which continues it. Not for the DB version (2.x);
> `logseq-cli doctor` tells you which one you have.
> [Why](#this-is-a-tool-for-file-based-graphs).

## What you can do with it

You say what you want; your agent runs the command. Describe your style once
in your agent's instructions, a timestamp before each log entry say, rather
than in every request. Each example says what the tool does that editing the
files would not.

- **Keep a work log.** *"Log: we decided to ship on Friday."* It lands on
  today's page, under your journal heading once you have set one, as
  `**14:30** Decided to ship on Friday`, proven written, while you keep
  typing. Dictated text with several lines goes in as one block, indented
  `- ` lines as its children, and any other page works the same way.
  `logseq-cli add-journal-block --content "**14:30** Decided to ship on Friday"`
- **Find your tasks, wherever they stand.** *"What is still open from
  yesterday and today?"* One call instead of reading every journal, and a
  task you pulled into a later day's journal by referencing it is found on
  that day too, and listed once.
  `logseq-cli get-todos --from yesterday`
- **Tick one off.** *"Mark the release task in Project Alpha as done."* The
  marker changes on the block itself, and the agent can see the change before
  it is made.
  `logseq-cli set-todo-status --content "release" --page "Project Alpha" --status DONE --dry-run`
- **Prepare a meeting.** *"What have I noted about Alex lately?"* Only the
  blocks that link to Alex reach the agent, three per page unless it asks for
  more, not the pages they sit on.
  `logseq-cli get-backlinks --page "Alex" --with-context`
- **Keep track of who owes what.** *"What am I waiting for from Alex?"* The
  tasks marked WAITING or TODO that mention Alex, filtered before the agent
  sees them.
  `logseq-cli get-todos --status WAITING --status TODO --match "Alex"`
- **Find where you wrote about something.** *"Where did I note the
  migration plan?"* The matching blocks, ten at most, with a note when there
  are more, instead of the agent opening page after page.
  `logseq-cli find-block --content "migration plan" --limit 10`
- **Run it on a schedule.** A morning cron job running
  `examples/carry-todos-to-today.sh 7 --write`
  ([the script](examples/carry-todos-to-today.sh)) carries every TODO, DOING,
  NOW or LATER journal task older than a week into today's journal as a ref, with no agent and no
  tokens involved (without `--write` it only previews). A scheduled agent run
  can start from an overview of the last month without its full text, and
  write a briefing or a summary of your mood into today's journal.
  `logseq-cli get-journal-summary --range "last 30 days" --no-content`

## What it will and won't do

| Your agent asks to… | logseq-cli |
|---|---|
| add to a page while you type in Logseq | does it; your cursor stays where it is |
| change the block you are typing in | refuses |
| delete a block or page that `((block refs))` elsewhere point into | refuses, unless told to go ahead |
| rename a page onto a name that already exists | refuses; Logseq would merge the two |
| report a write that did not land as done | never; every write is proven |
| show a change before making it | does it: `--dry-run` on every command that writes |
| undo a change | cannot; a deletion is final, so preview first |
| read more than fits in its context | cuts page and journal reads and searches to a size it asks for, and says what it left out |

A call that fails exits non-zero and says why; [AGENTS.md](AGENTS.md) is the
reference for an agent using the tool, including the reasons a write is
refused. The previews and refusals command by command are in
[docs/commands.md](docs/commands.md#previews-and-refusals).

### Bounded output

Reads grow with the graph. On a real one (nearly four years of daily entries)
the unbounded commands produce far more text than an agent can take in at once:

| Call (measured with 0.16.0 on 2026-09-27) | Output |
|------|--------|
| `get-journal-range` over 30 days | 134,410 chars |
| `get-journal-range --tail 7` | 91,618 chars |
| `get-journal-range --tail 7 --heading "## Log"` | 72,672 chars |
| `get-journal-range --max-chars 20000` | 19,981 chars, and a note on how to go on |
| `get-journal-summary --range "this week"` | 91,785 chars |
| `get-journal-summary --range "this week" --no-content` | 899 chars |

For scale: Claude Code passes on about 30,000 characters of a shell command's
output; past that the agent gets a 2,000-character preview and a file to read
in parts.

`get-journal-range` can skip days before fetching them (`--tail`, `--limit`)
and keep one section per day (`--heading`); `get-journal-summary` can drop the
bodies (`--no-content`); `get-page` and `get-journal-range` can be cut to a
number of characters and continued where the cut fell (`--max-chars`,
`--from-block`), and `find-block` and `get-backlinks` capped (`--limit`). A
cut is never silent. What each option does in detail:
[Bounding a read](docs/commands.md#bounding-a-read).

## Get started

Three steps, two of them in Logseq:

1. **Switch on Logseq's API server**, under *Settings → Features → HTTP APIs
   server*. An **API** icon appears at the top right. In its menu, under
   *Server configurations*, tick *Auto start server with the app launched*,
   so the server is back after a restart, then choose *Start server*. Under
   *Authorization tokens*, *+ Add new token* with a name and a value you make
   up; that value is your token.
2. **Put the token in your shell profile**, so neither you nor your agent has
   to pass it (a `--token` argument would show in `ps` and your shell
   history):

   ```bash
   export LOGSEQ_TOKEN="TOKEN"
   ```

   Open a new terminal and start your agent again afterwards; one started
   before sees no token.

3. **Tell your agent:** *"Install logseq-cli from
   github.com/muellerei/logseq-cli, run `logseq-cli doctor`, and add its
   skill."* Then ask for something: *"Log that I set this up."*

**To try it safely,** make an empty graph in Logseq, open it, and start
there; and ask your agent to show you every change as a preview
(`--dry-run`) before it makes it.

`doctor` tests each step separately — Python, packages, port, token, API,
graph kind, graph — and names the one that broke rather than leaving you to
guess, including whether it could read your graph. Exit 0 means everything
is ready.

The skill, [`skills/logseq-cli/SKILL.md`](skills/logseq-cli/SKILL.md), tells
an agent when to use the tool rather than the Markdown files, and the habits
that prevent damage. It follows the [Agent Skills](https://agentskills.io)
format; `npx skills add muellerei/logseq-cli` installs it (it needs Node.js).

### By hand

It needs Python 3.10 or newer (tested on 3.10–3.13) and two packages,
`click` and `requests` (plus `tomli` on 3.10), which the install pulls in. With
[pipx](https://pipx.pypa.io), which keeps it apart from your system Python:

```bash
pipx install git+https://github.com/muellerei/logseq-cli.git
```

or with plain `pip install git+https://github.com/muellerei/logseq-cli.git`.
Then run `logseq-cli doctor`. The server listens on `http://127.0.0.1:12315`
by default, which is what the CLI assumes. To work on the code, see
[CONTRIBUTING.md](CONTRIBUTING.md).

## Configuration

Two settings cover most setups:

| Variable | Default | Description |
|----------|---------|-------------|
| `LOGSEQ_TOKEN` | (empty) | Bearer token for authentication; `--token` overrides it |
| `LOGSEQ_JOURNAL_HEADING` | (none) | Default heading for journal writes: `add-journal-block`, `add-journal-content`, `add-block-ref` (e.g. `## Log`); overrides `[journal] default_heading` in the config file |

Every variable, the `--host`/`--port`/`--token` flags and the journal heading
in detail are in
[docs/configuration.md](docs/configuration.md#environment-variables-and-flags).

### Config file

Most of the CLI needs no configuration. A few things cannot be guessed, because
they describe your graph rather than Logseq: which namespace holds your project
pages, which property marks a person page, what your journal sections are
called. Those live in an optional file:

```bash
# Suggest one from your own graph (counts included, writes nothing with --dry-run)
logseq-cli init --dry-run
logseq-cli init

# Or start from the commented example
mkdir -p ~/.config/logseq-cli && cp config.example.toml ~/.config/logseq-cli/config.toml
```

A command that needs a setting you have not made says which one, and exits
non-zero rather than returning an empty result. See
[docs/configuration.md](docs/configuration.md) for every option, what happens
without it, and how to read the right values out of your own graph.

## Commands

The full reference — every command with its options, usage examples,
parameter and page aliases, the round-trip shortcuts and the scripting
examples — is in [docs/commands.md](docs/commands.md). `logseq-cli --help`
lists the commands, `logseq-cli <command> --help` a command's options.

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

No write is taken on Logseq's word: each is proven by what Logseq returns or read back, and none overwrites a block you are editing.

Logseq's HTTP API answers almost every write with `null`, whether it wrote or
not, and a write it threw on with HTTP 200 and an error object
(`server.cljs` `invoke-logseq-api!`, `listener.cljs` `invokeLogseqAPI`,
tag 0.10.15). A command that trusts
the status code reports "Added N block(s)" over a journal entry that was never
written — and for a journal entry, nothing else will ever tell you;
`copy-block --remove` once deleted its source against a copy that had not
landed. So the proof sits in the API client, inside each write method, where
no command can go around it, and the client refuses to send a method it has
no rule for. An insert is proven by its answer, which carries the new block
and is `null` for one Logseq did not write. Every other write is proven by
reading back what it should have changed: the text of an updated block, a
property's value, the blocks a batch added, a moved block in its new place, a
removed block gone, a renamed page under its new name, a created page found
under its name. The note above `_METHODS` in `logseq_cli/api.py` says how
each one is proven and why.

"Proven" has two limits. The read goes to Logseq's database, not to the
Markdown file, which follows about 1.8 s later (measured on 0.10.15). And a
write from elsewhere that lands between the CLI's write and its read makes a
write that did land fail as `write_not_verified`; the error shows what was
expected and what was read, so read the block before retrying.

The second half of the sentence answers a loss no read-back can catch.
Measured on Logseq 0.10.15: a write to the block being edited replaced the
editor content at once and dropped what had not been saved; Logseq answered
`null`, and a read right after still showed the old text. `updateBlock` on an
open block writes into the editor's state (`api.cljs` `update_block`). So before a
write that changes a block, the CLI asks Logseq which block is open and
refuses with `open_in_editor` if the write would change it: the block itself,
for a removal or a move also a block below it, for a page deletion or a
rename a block of the page, and for a rename also a block that links to the
page, whose link Logseq rewrites. An insert is not refused: Logseq saves the open block before
it inserts (measured), and the CLI inserts with `focus: false`, so the cursor
stays where it was. The one insert refused is a text with a ref to the open
block while that block has no `id::` yet, since storing the id writes into
it. A write of several blocks goes block by block while a block is open,
since Logseq's batch insert opens its last block in the editor once the page
is on screen. What the word does not cover is a block entered in the
milliseconds between the CLI's question and its write.

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

A rename had a silent loss of its own. `renamePage` onto a name that exists
merges the two pages (`merge-pages!` in `page.cljs` `rename!`, 0.10.15), without the
confirmation Logseq's own UI asks for; measured on 0.10.15, it answered
`null` while the renamed page's blocks moved to the other page and the
renamed page was gone. `rename-page` refuses such a name, and an empty one,
before anything is sent.

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
erfolgreich" as positive, 16% of positive hits in a 90-day sample; and
`find-knowledge-gaps` reported 596 orphans that were mostly Logseq's own
by-products. None of that was caught by tests asserting that output exists —
it took reading the numbers next to a graph whose real answer was known. A
plausible number that gets believed is worse than an obvious failure, so these
commands now measure one defined thing each and agree with one another.
See [0.9.0, Fixed](CHANGELOG.md#090---2026-09-14).

### Output is bounded because the consumer has a context limit

An agent reading a month of journals gets 431,996 characters, against about
30,000 that Claude Code passes on from a shell command — past that, the agent
gets a 2,000-character preview and a file it would have to read in parts, so
either the answer is a fragment or the reading costs the context it was meant
to save. So `--tail`
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
with what Logseq shows in its place, followed by the page it came from
(`the actual text ↳ Meeting Notes`), and descends into child blocks so a nested
quote resolves too. Logseq shows the target's first line, without a heading's
`#`s and with the dates of its `SCHEDULED:` and `DEADLINE:` lines; a target
that opens with something else (a code block, math, a quote, a table, HTML, a
rule, a `#+BEGIN_` block or properties) has no such line and shows its body,
here joined into one line. The resolved text stays on the line of the ref, so the target's
properties stay out, its `id::` line included: on a line of their own they
would read as the referencing block's. For the whole target, run `get-block`
on the uuid. A reference whose target is gone is left as
`((uuid))` rather than dropped or blanked, and `get-page` names it on stderr: a
hole you can see beats a sentence that silently lost a clause.

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

## Contributing and credits

The package layout, one line per module, and how commands are registered are
in [CONTRIBUTING.md](CONTRIBUTING.md#project-structure); the decisions behind
them are in [docs/adr](docs/adr/).

The core commands are inspired by [joelhooks/logseq-mcp-tools](https://github.com/joelhooks/logseq-mcp-tools), extended with property management, page operations, and property-based queries.

MIT licensed, see [LICENSE](LICENSE).
