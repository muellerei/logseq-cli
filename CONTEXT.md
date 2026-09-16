# logseq-cli

A command-line interface to a Logseq graph over Logseq's local HTTP API, built
for a caller that is a script or an AI agent rather than a person at a prompt.

## Language

### The CLI surface

**Command**:
One operation the CLI performs, implemented as one decorated function.
There are 37.
_Avoid_: subcommand, verb, action

**Command Name**:
A string that invokes a Command. A Command usually has one, but may have
several — `remove-block` and `delete-block` reach the same Command. There are
38 names for 37 Commands.
_Avoid_: command alias, alias

**Canonical Name**:
The Command Name a Command is defined under, and the one the documentation
uses. `remove-block` is canonical; `delete-block` exists so that the most
common wrong guess works instead of erroring out.
_Avoid_: primary name, real name

**Parameter Alias**:
A second spelling of one option, accepted interchangeably. Every option taking
a page name accepts both `--page` and `--name`.
_Avoid_: alias, option alias, flag alias

**Registry**:
The set of Command Names Click resolves against, i.e. `cli.commands`. It is
the only authority on which Commands exist; the help text is a rendering of
it, not a second source.
_Avoid_: command list, command table

### The graph

**Page Alias**:
A Logseq alias — an alternative name under which a page can be addressed
inside the graph. Unrelated to Command Name and Parameter Alias, and not
resolved by Logseq's API on the caller's behalf.
_Avoid_: alias (unqualified)

**Journal Page**:
The page Logseq keeps for one calendar day. Addressed by date, not by title.
_Avoid_: daily note, diary entry

**Block**:
One bullet in a page, with an identity of its own and possibly children.
_Avoid_: node, item, entry

### Guarantees the tool makes

**Refusal**:
The tool declining a call it will not perform — bad input, a missing page, an
ambiguous match. Distinct from a failure: nothing broke, and no data was
touched.
_Avoid_: error, rejection

**Strict Insert**:
The contract that a write either lands where it was asked to land or is
refused. It exists because Logseq answers some failed writes with HTTP 200 and
a `null` body, so a write is verified after the fact rather than trusted.
_Avoid_: safe write, verified write

**Dry Run**:
A preview of a write that performs no write. Available on every Command that
writes.
_Avoid_: simulation, test mode
