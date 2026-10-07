# Limiting what an agent can do

An agent that drives `logseq-cli` can write anywhere the token allows. This
page is about making it unable to write at all: `read_only`, one switch in the
config file.

It protects against an agent that makes a mistake. It does not protect against
one that sets out to get around it; [what it does not stop](#what-it-does-not-stop)
says what stays within an agent's reach and how to shrink that.

## The recipe

In `~/.config/logseq-cli/config.toml` (`mkdir -p ~/.config/logseq-cli` first;
`$XDG_CONFIG_HOME/logseq-cli/config.toml` instead when that variable is set;
see [configuration.md](configuration.md#where-the-file-is-looked-for) for the
other places the file can live):

```toml
[safety]
read_only = true
```

Then check that it applies:

```bash
logseq-cli doctor
```

```text
  [??  ] read_only: on (config /home/you/.config/logseq-cli/config.toml)
…
Ready to read; writes are off.
```

`doctor` shows the state of `read_only` on every run, on or off, and names
where it comes from. The closing line needs a reachable Logseq and a token like
any other `doctor` run; without them it says "Not ready." for those reasons,
not because of `read_only`. Between the two lines it prints its other checks,
among them a note that a config without `[graph]` settings leaves some queries
unconfigured; that is about other settings. If the line says `off (no config file found)` or
`off (no [safety] in …)`, the switch is not in force, whatever the file you
meant says: the CLI is reading another file, or another `HOME`.

With the switch on, every command that writes refuses before it sends its
first request:

```text
$ logseq-cli add-journal-block --content "x"
Error: Writes are off: read_only = true in [safety] of /home/you/.config/logseq-cli/config.toml. Nothing was written.
```

The refusal comes before any request, so it needs neither a token nor a
running Logseq: this is a check you can make without touching the graph.

The exit status is non-zero. Under `--json` (an option of the command, after
its name) stderr holds one object:

```json
{
  "error": "Writes are off: read_only = true in [safety] of /home/you/.config/logseq-cli/config.toml. Nothing was written.",
  "reason": "read_only",
  "writes_landed": 0,
  "source": ["config"],
  "config_path": "/home/you/.config/logseq-cli/config.toml"
}
```

`source` lists what switched writes off (`config`, `env`, `flag`);
`config_path` is `null` when no config file was found.

What the switch covers:

- Every command that writes refuses, **`--dry-run` included**. A preview that
  says "would write" when the real run cannot would lie about it. An agent
  under `read_only` cannot plan a write for you to run; if you want that, do
  not set `read_only`, and have it preview with `--dry-run`.
- Commands that only read run as before, and so do `--help` and `init`
  (which writes the config file, never the graph).
- Behind the commands, `LogseqAPI` refuses a second time before any write
  method leaves the process, so a write reaching the network by another path
  is stopped too.

## Requiring a precondition

`read_only` is a lock: nothing writes. `require_preconditions` is an
obligation: a command that changes a block it read must say what it read, and
refuses when it does not.

```toml
[safety]
require_preconditions = true
```

```text
$ logseq-cli set-todo-status --id 6650a1b2-0000-4000-8000-000000000001 --status DONE
Error: A precondition is required: require_preconditions = true in [safety] of /home/you/.config/logseq-cli/config.toml. Pass --expect-hash or --expect-marker. Nothing was written.
```

Under `--json` the object has `reason: precondition_required` and the same
`source` and `config_path` as `read_only`, plus `options`, the ones that would
do. The refusal comes before the first request, `--dry-run` included, and
`read_only` is checked before it: with both on, a call without a precondition
is refused as `read_only`. Set it the same three ways as `read_only` (config,
`LOGSEQ_CLI_REQUIRE_PRECONDITIONS`, `--require-preconditions`); all only
tighten, and a config that cannot be read refuses the commands that ask for a
precondition. `doctor` shows it on every run.

The obligation belongs to the call, not to the command: a form of a command
that changes no block that was read is not asked for one. Today
`set-todo-status` takes `--expect-hash` and `--expect-marker`, `update-block` `--expect-hash`, `remove-block` (and its alias `delete-block`) `--expect-tree-hash`;
`set-block-property`, `remove-property --id`, `move-block` and
`copy-block --remove` take `--expect-hash`. `add-journal-block --upsert-heading` owes `--expect-hash` once it has picked the block it replaces, and only when there is one: with no match it creates the block and owes nothing. `remove-property --page` and
`copy-block` without `--remove` change no block that was read: they owe
nothing, and the option is a usage error there.

### Why it is off by default, and when to turn it on

A default of "on" would change what every script and person calling
`update-block`, `remove-block` and the other six commands does today: calls that
pass no hash would start to refuse with `precondition_required`. That is reason
enough, but not the whole one. The stronger reason is friction. The output of a
write does not carry the block's new hash yet, so a second write to the same
block with the hash from the first read is refused (the block did change, by
your own first write). With the switch on, the only way through is to read the
block again before every write. That ritual protects no more than the re-read
each command already does between its own read and its write (`block_changed`),
so a mandatory switch costs more than it gives until the write outputs return a
hash. It is off for that reason, and it stays a choice.

Turn it on when an agent writes and you also edit the graph yourself while it
works: then every write has to show the state it was decided on. Without an
agent, or without edits in parallel, it adds nothing. Without the switch the
protection holds only for calls that pass a hash; a call that passes none is
written as before.

A precondition and `block_changed` guard different things. `block_changed` is
the command's own re-read: between its read and its write, did the block change?
A precondition is the caller's: is the block still what I read, minutes ago,
when I decided what to write? A precondition that fails is `precondition_failed`;
after it, read the block again and decide again. Repeating the same change with
the fresh hash writes the old decision over the change that was made.
[`examples/safe-update-block.sh`](../examples/safe-update-block.sh) does it that
way.

### What it does not do

- **It is no compare-and-swap.** Reading and writing stay two requests to
  Logseq, and a change in the moment between the check and the write is not
  caught by the precondition. `update-block` narrows that gap with its own
  re-read (`block_changed`); nothing closes it, because the API has no
  conditional write.
- **It does not cover the copy in `copy-block --remove`.** The command copies
  the subtree block by block and deletes the source at the end, so a child
  added under the source while the copy runs is deleted with it. The window
  grows with the size of the tree, and `--expect-hash` checks the state before
  the copy starts, not during it.
- **The hash sees content, not position.** `move-block --expect-hash` does not
  notice that the block was moved since it was read; it notices that its text
  or properties changed. `remove-block` takes the hash of the block and
  everything under it (`--expect-tree-hash`), because the hash of the block
  alone would not see a child added since.
- **Write outputs carry no new hash.** Whoever changes the same block twice
  reads it again in between.
- **It guards against a mistake, not against an agent that sets out to avoid
  it.** An agent can read the block just before writing and pass what it finds,
  which proves nothing about what it decided on. The switch makes the honest
  path the required one; it does not make the other impossible.

## Three ways to set it, and how binding each is

The forms below are written for `read_only`. `require_preconditions` has the
same three, with `LOGSEQ_CLI_REQUIRE_PRECONDITIONS` and `--require-preconditions`,
and it only tightens in the same way.

| Way | Set by | Holds as long as |
|-----|--------|------------------|
| `[safety] read_only = true` in the config file | you, in a file | the agent cannot change the file |
| `LOGSEQ_CLI_READ_ONLY=1` in the environment | whoever starts the agent | the agent does not remove it from its command |
| `--read-only` on the command line, before the command name (`logseq-cli --read-only add-journal-block …`) | the agent itself, told to by a skill or a prompt | the agent keeps passing it |

The first is the one to rely on. The environment variable is a step weaker: an
agent that builds its own command line can leave it out. The flag is a
restraint the agent puts on itself, against its own slips, and it is exactly
as good as the instruction to pass it.

All three only tighten. `LOGSEQ_CLI_READ_ONLY=false` or a call without
`--read-only` leaves a config that says `true` in force, and there is no
`--no-read-only`. `1`, `true`, `yes` and `on` switch it on; `0`, `false`, `no`,
`off` and an empty value do nothing; any other value (`flase`) switches it on
and says so on stderr, because a switch that a typo turns off is worse than
one a typo turns on.

## Keeping the config file out of the agent's reach

A switch the agent can edit is a request. `init` cannot lift it: it keeps the
`[safety]` section of the file it overwrites, and of the config in use when it
writes a new file at any path the config is looked for (`init --output
~/.config/logseq-cli/config.toml` with the config in `~/.logseq-cli.toml`).
But an agent that can write the file can. For Claude Code, from its
[permissions](https://code.claude.com/docs/en/permissions) and
[sandbox](https://code.claude.com/docs/en/sandboxing) documentation:

- An `Edit` deny rule (`Edit(~/.config/logseq-cli/**)`) applies to Claude's
  own file tools, to file commands Claude Code recognizes in Bash (`sed`,
  `tee`) and to the targets of redirections (`> file`). It does **not** apply
  to a subprocess that writes a file itself: `logseq-cli init --force --output
  <the file>`, or a Python script.
- The sandbox does, because the operating system enforces it for every
  process a command starts:

  ```json
  {
    "sandbox": {
      "enabled": true,
      "filesystem": {
        "denyWrite": ["~/.config/logseq-cli", "~/.logseq-cli.toml"]
      }
    }
  }
  ```

  Not tried here with `logseq-cli` inside it. The sandbox also limits network
  access, and `logseq-cli` talks to Logseq on `127.0.0.1`; run `logseq-cli
  doctor` from inside it before relying on the setup.

Other agent tools have their own mechanisms. What matters is the same: the
file is not writable by the process the agent runs in.

## What it does not stop

- **An agent with the token.** It can send requests to Logseq's HTTP API
  itself, with `curl` or a script, and none of it passes through this CLI.
- **An agent that can write files.** It can edit the Markdown files of the
  graph directly, or the config file, if nothing keeps it from either.
- **The choice of file.** `LOGSEQ_CLI_CONFIG` and `XDG_CONFIG_HOME` decide
  which config is read. Whoever sets the agent's environment can point it at a
  file without the limit. A different `HOME` finds no config at all, and
  without a config nothing is off: `doctor` says `off (no config file found)`.
- **A refusal is not a report.** The CLI refuses; whether the agent then
  tells you or tries another way is up to the agent.

## When it seems not to apply

The rows are written for `read_only`. `require_preconditions` follows the same
rules: `doctor` shows it on a line of its own, and a wrong value, a misplaced
key or an unreadable file gives the same errors.

| You see | It means |
|---------|----------|
| `doctor`: `read_only: off (no config file found)` | No config file at any of the places it is looked for, for this user and environment |
| `doctor`: `read_only: off (no [safety] in …)` | A file was found, but it has no `[safety]` section: another file than the one you edited, or a typo in the section name |
| `doctor`: `read_only: unknown, commands that write refuse until this is fixed: …` | The config cannot be used (one of the errors below, named after the colon). Whether `read_only` is meant to be on cannot be told, so commands that write refuse with `reason: config_error` |
| `Error: … unknown key `readonly` in [safety], did you mean `read_only`?` | A misspelt key. Commands that write refuse with `reason: config_error` until it is fixed; commands that only read run, and most of them say nothing about it, so check with `doctor`, which shows it every time |
| `Error: … `read_only` is at the top level, where it does nothing; `read_only` belongs under [safety].` (or `is in [journal]`, `is in [saftey]`) | The key is outside `[safety]`: at the top level, in another section, under a misspelt `[saftey]`, or as `[journal.safety]` (a `safety` table nested in another section: `[safety] is a top-level section`) |
| `Error: … [safety] read_only must be true or false, got 'yes'.` | The value is a string, not a TOML boolean |
| `Error: … is not valid TOML` | The file does not parse. Commands that write refuse (`reason: config_error`) rather than run without what the file may hold. Fix it; removing it lifts the limit |
| `Error: LOGSEQ_CLI_CONFIG points at …, which does not exist` | The variable names a file that is not there. Commands that write refuse; commands that only read warn and go on |
| `init` refuses with `reason: config_exists` | It writes to the config in use, which exists. `init --force` overwrites it and keeps `[safety]`; it refuses a file it cannot parse |

Only `[safety]` is checked strictly. A misspelt key in another section is
ignored, as it always was; `doctor` names a section it does not read
(`[jurnal]`).

## When an agent meets it

The agent should stop and tell you. It should not retry, edit the config,
unset the variable, change `HOME`, or write the Markdown files instead.
[AGENTS.md](../AGENTS.md) says so in the terms an agent reads.
