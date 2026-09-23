---
name: logseq-cli
description: Read and change a Logseq graph through the running Logseq app, with the logseq-cli command. Use when the user wants something looked up in, added to or changed in their Logseq notes or journal, or names a Logseq page, a journal date, a block UUID or a ((block ref)).
license: MIT
---

# logseq-cli

`logseq-cli` talks to the Logseq desktop app over its local HTTP API. It
works on file-based (Markdown) graphs; Logseq 2.x DB graphs are not
supported.

## Why the tool and not the Markdown files

A Logseq graph is a folder of Markdown files, but the answers live in
Logseq's database, which the files do not show:

- A page name means what it means in Logseq: most commands follow an alias
  to the page it names, and the ones that would delete, rename or create a
  page refuse an alias rather than guess.
- Backlinks, TODOs across the graph, properties and queries come from the
  database, not from a text search.
- Logseq answers an insert it dropped with HTTP 200 and no block; the tool
  reports that as a failure, and the commands that add blocks print the
  UUIDs they made.

So while Logseq runs, read and write through `logseq-cli`. When it is not
running, the tool cannot work, and AGENTS.md says how to use the files.

## First: `logseq-cli doctor`

Run it before anything else. It checks Python, the port, the token, the API
and the graph kind, and on failure names the step that is missing (exit 1),
for example the token from Logseq's API settings, passed as `LOGSEQ_TOKEN`.

## Habits that prevent damage

- **Name a journal by its date, not by a page name you build.** The journal
  page's name follows the graph's date format, and a writer given a page
  name that does not exist creates that page. The journal writers take
  `--date`, `add-block-ref` takes `--journal-date`, and
  `get-journal-range --from --to` reads them.
- **Preview destructive writes with `--dry-run`**, which every write takes:
  `delete-page`, `remove-block`, `move-block`, `copy-block --remove`,
  `rename-page`, `update-block` and `replace-text` show what they would
  change and write nothing. Deletes also refuse while `((refs))` from
  elsewhere point into the target; `--ignore-refs` overrides that, so name
  the refs to the user first.
- **Read large pages in parts**: `get-page --outline` lists the headings with
  their UUIDs, `get-page --heading "## X"` returns one section, and
  `get-page --max-chars N` cuts the output to size and names how to go on.
  `find-block --limit N` caps what is printed.
- **Trust the exit status**: 0 is success, 1 a failure, 2 input the tool
  refused. With `--json`, stdout holds only data; errors go to stderr,
  mostly as a JSON object, some as a plain `Error:` line.

## Everything else

The command reference, the JSON shapes and the rules for block text, ids and
properties are in
[AGENTS.md](https://github.com/muellerei/logseq-cli/blob/main/AGENTS.md).
