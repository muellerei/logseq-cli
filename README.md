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
typing in. [How that works](docs/design.md#writes-are-verified-not-assumed).

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
> [Why](docs/design.md#this-is-a-tool-for-file-based-graphs).

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
| show a change before making it | does it: `--dry-run` on every command that writes (not under `read_only`, below) |
| write when it was told to only read | refuses: `[safety] read_only = true` switches every write off, before the first request |
| undo a change | cannot; a deletion is final, so preview first |
| write while Logseq is closed | cannot; your agent is told to stop rather than edit the files |
| read more than fits in its context | cuts page and journal reads and searches to a size it asks for, and says what it left out |

A call that fails exits non-zero and says why; [AGENTS.md](AGENTS.md) is the
reference for an agent using the tool, including the reasons a write is
refused. The previews and refusals command by command are in
[docs/commands.md](docs/commands.md#previews-and-refusals).

### Limiting what an agent can do

To let an agent read your graph but not change it, put this in your config
file:

```toml
[safety]
read_only = true
```

and run `logseq-cli doctor`: it shows `read_only: on (config …)` when the
switch is in force, and where it comes from when it is not. Every command that
writes then refuses before its first request, with `reason: read_only`.

It protects against an agent that makes a mistake, not against one that sets
out to get around it: an agent with the API token can still send requests
itself, and one that can write files can edit the config or the Markdown files.
[docs/safety.md](docs/safety.md) covers how binding each way of setting it is
(config file, environment variable, `--read-only`), how to keep the config file
out of an agent's reach, and what to check when it seems not to apply.

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

The same file can switch writes off for an agent (`[safety] read_only`, see
[Limiting what an agent can do](#limiting-what-an-agent-can-do)), and keep the
empty blocks you leave at the end of a section last, so entries land before them
and no empty line is left (`[graph] keep_empty_blocks_last`, off by default, see
[docs/configuration.md](docs/configuration.md#graph-keep_empty_blocks_last)).

## Commands

The full reference — every command with its options, usage examples,
parameter and page aliases, the round-trip shortcuts and the scripting
examples — is in [docs/commands.md](docs/commands.md). `logseq-cli --help`
lists the commands, `logseq-cli <command> --help` a command's options.

## Design notes

The decisions behind the tool, most of them out of a defect and all of them
one rule applied in different places: an answer must not have two possible
causes. Each is written up in [docs/design.md](docs/design.md):

- [Writes are verified, not assumed](docs/design.md#writes-are-verified-not-assumed):
  Logseq answers almost every write with `null`, whether it wrote or not, so
  each write is proven, and none overwrites the block open in the editor.
- [Writes can be switched off, checked twice](docs/design.md#writes-can-be-switched-off-checked-twice):
  a limit that only tightens, refused before the first request and again
  where a request leaves the process.
- [Query values are escaped in one place](docs/design.md#query-values-are-escaped-in-one-place):
  values entering datalog queries go through one layer rather than being
  escaped at each call site.
- [Analysis output is measured against a real graph](docs/design.md#analysis-output-is-measured-against-a-real-graph):
  counts no test caught, such as 438 open tasks for a graph with 256, were
  found by checking the output against a real graph.
- [Output is bounded because the consumer has a context limit](docs/design.md#output-is-bounded-because-the-consumer-has-a-context-limit):
  a month of journals is far more than an agent can hold, so reads are cut to
  size, journal days can be skipped before they are fetched, and no cut is
  silent.
- [A block reference is not readable on its own](docs/design.md#a-block-reference-is-not-readable-on-its-own):
  a `((uuid))` tells a reader nothing, so page and journal reads can resolve
  it in place.
- [A task is where it stands, not only where it was written](docs/design.md#a-task-is-where-it-stands-not-only-where-it-was-written):
  a task carried forward by ref is found on the days it was carried into.
- [Exit status: done or not done, and no resume](docs/design.md#exit-status-done-or-not-done-and-no-resume):
  zero means done, non-zero means not, and the number says nothing more.
- [A write takes its target by name, not from a pipe](docs/design.md#a-write-takes-its-target-by-name-not-from-a-pipe):
  one explicit target per write; bulk work is a loop, each write with its own
  checks.
- [This is a tool for file-based graphs](docs/design.md#this-is-a-tool-for-file-based-graphs):
  the Markdown line of Logseq, by preference; the DB version is out of scope.

## Contributing and credits

The package layout, one line per module, and how commands are registered are
in [CONTRIBUTING.md](CONTRIBUTING.md#project-structure); the decisions behind
them are in [docs/adr](docs/adr/).

The core commands are inspired by [joelhooks/logseq-mcp-tools](https://github.com/joelhooks/logseq-mcp-tools), extended with property management, page operations, and property-based queries.

MIT licensed, see [LICENSE](LICENSE).
