# A page name means what it means in Logseq

Logseq's HTTP API does not resolve a page alias: `getPage` on one answers the
alias's own entity, a stub without blocks. Every command that takes a page
name therefore resolves it itself, through one function
(`logseq_cli/pagenames.py`), by the rule Logseq's UI follows in
`get-redirect-page-name` (`frontend/db/model.cljs`, 0.10.15): a name whose
page is empty or a placeholder, and that a page names in its own `alias::`
property, means that page; any other name means itself. Reads and writes go to
the same page.

## Considered Options

**Reads follow the alias, writes refuse it.** Safe, and it was the first
design. Rejected: it gives one name two meanings, a page for reading and an
error for writing, where Logseq has one. Refusing also only stops the shadow
page a write to an alias made; following means none is made.

**Resolve inside `LogseqAPI`.** Every call would pass through it. Rejected:
the API layer cannot tell a name meant to be created from one meant to be
found, so `create-page` would be redirected too, and page names also reach
Logseq inside Datalog strings and Python filters it never sees.

**A Click parameter type.** Resolution at parse time, in one place. Rejected:
`--json` may not be parsed yet when `--page` is converted, and a failure there
ends as a usage error with exit 2.

**Find the page by `:block/alias` and `:block/file`.** Rejected after
measurement: `:block/alias` links every page of an alias group, and a write to
an alias gave it a file, so two pages matched. The source's own `alias::`
property is what Logseq's `get-alias-source-page` checks, and it held in every
state measured.

## Where the CLI differs from Logseq

- An alias two pages claim is refused, naming both; Logseq takes the first.
- `delete-page` and `rename-page` take the page's own name. They cannot be
  undone, and an alias leaves open whether the alias or the page is meant.

## Consequences

A command with a page option calls `follow_page` (`output.py`) where the name
comes in, which resolves it and says so on stderr in one call;
`tests/test_alias_resolution.py` runs each one with an alias, asserts that no
API call after the resolution names it, and fails for a page option that has
neither a case nor a stated exemption. `get-todos --page` and
`suggest-connections --focus` are exempt: they filter by page name and name no
page.

A page that `getPage` reports with a file or with page properties is taken as
itself without reading its blocks: an alias Logseq made has neither
(measured), and a large page is not read whole to find out. A file emptied
down to one blank block would be missed and mean itself, as every name did
before.

The lookup relies on `:block/original-name` and `:block/properties`, fields of
the file graph. On the DB version (2.x), which this tool does not support, an
alias is not followed. Under `--json`,
`page` keeps the name given and `alias_of` names the page used, so a caller
that matches results to its own names keeps finding them.
