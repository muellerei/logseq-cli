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

## Three ways to set it, and how binding each is

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

| You see | It means |
|---------|----------|
| `doctor`: `read_only: off (no config file found)` | No config file at any of the places it is looked for, for this user and environment |
| `doctor`: `read_only: off (no [safety] in …)` | A file was found, but it has no `[safety]` section: another file than the one you edited, or a typo in the section name |
| `doctor`: `read_only: unknown, commands that write refuse until this is fixed: …` | The config cannot be used (one of the errors below, named after the colon). Whether `read_only` is meant to be on cannot be told, so commands that write refuse with `reason: config_error` |
| `Error: … unknown key `readonly` in [safety], did you mean `read_only`?` | A misspelt key. Commands that write refuse with `reason: config_error` until it is fixed; commands that only read run, and most of them say nothing about it, so check with `doctor`, which shows it every time |
| `Error: … `read_only` is at the top level, where it does nothing; `read_only` belongs under [safety].` (or `is in [journal]`, `is in [saftey]`) | The key is outside `[safety]`: at the top level, in another section, under a misspelt `[saftey]`, or as `[journal.safety]` (a `safety` table nested in another section: `[safety] is a top-level section`) |
| `Error: … [safety] read_only must be true or false, got 'yes'.` | The value is a string, not a TOML boolean |
| `Error: … is not valid TOML` | The file does not parse. Commands that write refuse (`reason: config_error`) rather than run without what the file may hold. Fix it; removing it lifts the limit |
| `init` refuses with `reason: config_exists` | It writes to the config in use, which exists. `init --force` overwrites it and keeps `[safety]`; it refuses a file it cannot parse |

Only `[safety]` is checked strictly. A misspelt key in another section is
ignored, as it always was; `doctor` names a section it does not read
(`[jurnal]`).

## When an agent meets it

The agent should stop and tell you. It should not retry, edit the config,
unset the variable, change `HOME`, or write the Markdown files instead.
[AGENTS.md](../AGENTS.md) says so in the terms an agent reads.
