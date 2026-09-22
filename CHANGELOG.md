# Changelog

All notable changes to `logseq-cli` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `scripts/check-links.py` checks the relative links and heading anchors across
  the Markdown files. Every one of them claims a file and a heading exist, and
  nothing verified that, so a rename broke them without any sign. The anchor
  rule is the part that is easy to get wrong: GitHub drops punctuation before
  turning spaces into hyphens, so an em dash in a heading leaves both its
  spaces behind and the anchor takes two hyphens, not one. Link syntax shown
  inside fenced blocks and inline code is not a link and is skipped — this
  project documents Markdown graphs, so examples are the normal case. So are
  generated trees and the gitignored `local/`, after an earlier version read
  them and reported a break no contributor could have seen.

### Fixed

- The build now ships `logseq_cli.commands`. `pyproject.toml` lists packages
  explicitly, which was right while the package was flat and became wrong the
  moment it had a subpackage: setuptools does not infer one from an explicit
  list. No release was ever affected — the subpackage and the list entry landed
  in the same commit — but the failure mode is worth naming, because it is
  invisible to the tests. `pip install -e .` links the source tree, so an
  editable install imports the subpackage regardless; what a user would have
  installed is a CLI that starts and has no commands.

- `get-backlinks --with-context --limit` accepted a negative value and answered
  with less data and a count larger than the page held. Three linking blocks
  came back as two, with `... 4 more not shown`, exit code 0, in both output
  formats. The cap is applied as a slice and the withheld count was derived
  from the cap, so one bad value broke both halves at once: `blocks[:-1]` drops
  the *last* block instead of capping, and `len(blocks) - (-1)` exceeds what
  exists. `0` is valid here and means "keep all", which is what made the wrong
  input reachable rather than exotic — a caller who knows that reaches for `-1`
  as "all the more so", and `examples/carried-over-todos.sh` relies on the same
  meaning for `--refs-limit`. The boundary is therefore `< 0`, not `< 1`.

  Two layers, because the guard alone would leave the count derived from a
  number the caller supplies: the option now rejects a negative value before
  any page is read, and `withheld` is counted against the blocks actually kept.
  `get-journal-range` has computed its `omitted` that way all along. Tested at
  the boundary from both sides, in both formats and in batch mode, and the
  second layer is tested where it lives — on the extractor itself, since the
  guard otherwise hides it. See [#17](https://github.com/muellerei/logseq-cli/issues/17).

- Three more numeric options accepted a negative value. None of them announced
  it, which is why none had been found: they answered a different question than
  the one asked, with exit code 0. `analyze-graph --days -1` moved the cutoff
  into the future, so "recently updated" came back empty on a graph that had
  been edited minutes earlier. `init --days -1` dropped the *oldest* journal
  from the sample instead of limiting it, so the suggestion rested on a quietly
  different set than the one asked for. `suggest-connections
  --max-suggestions -1` dropped the weakest suggestion — and, as the entry
  below records, misreported the total while doing it. All three now refuse the
  value before reading anything.

- `find-block` validated `--limit` after running its query, so a value it was
  going to refuse still cost a full graph read first (measured when the cap was
  added: 71ms and 473KB for 1382 matches). The check now runs before the query,
  which is where the other guards already sat. Found by a test asserting that a
  refusal costs no API call — not by reading the code.

- `get-journal-range` refused a bad `--tail`/`--limit`/`--from` through Click,
  which exits 2 with a usage dump on stderr, where the checks a command makes
  itself use `fail()` — exit 1 and, under `--json`, an error object. The whole
  point of that helper is that a caller parsing stderr as JSON is never handed
  prose instead, and this command speaks `--json`, so an agent asking for a
  structured answer got an unparseable one. It now refuses the same way as the
  rest. Found by an independent review of the commits above, not by the sweep,
  which asserted only a non-zero exit and so covered the difference up; the
  sweep now checks the exit code and the JSON shape of the refusal.

  Not converted: the shared parsers in `helpers.py`, which raise
  `BadParameter` across thirteen call sites. An unparseable `--from nonsense`
  therefore still exits 2 while a reversed `--from`/`--to` exits 1 — the same
  user error reported two ways. Changing that touches every command that parses
  a date, a tree or a `--content-file`, which is a separate piece of work with
  its own blast radius; `CONTRIBUTING.md` names the gap rather than implying it
  is closed.

- `set-property`, `set-block-property` and `--property KEY=VALUE` wrote any key
  they were given and reported success. A shell loop that passed
  `"type Project"` as one argument left a page whose first line had become a
  bullet block, with the key duplicated in the file and reported by
  `get-properties` under a camel-cased name the file did not contain.

  The cause sits between two parts of Logseq that disagree.
  `upsertBlockProperty` stores the key as handed over and writes `key:: value`
  into the file. The parser that reads the file back lower-cases the key, reads
  `_` as `-`, and drops the line unless the result is a valid EDN keyword. Until
  the next re-index the database holds one thing and the file another. Which
  keys the parser keeps, renames or drops was measured one key at a time
  against Logseq 0.10.15, not taken from its source alone: `/` for instance
  passes the source's keyword check, but `a/b` comes back as `b` and `a/`,
  `/a` and `a/b/c` not at all.

  Keys are now checked before the first API call. The parser's case and `_`
  renames are applied here as well, so the database gets the key the file will
  be read back as, and a note on stderr says so (`'Status'` is stored as
  `'status'`). Everything the parser drops is refused with the reason. So is
  its third rename, `custom-id` to `id`, found in review and then measured:
  `custom-id:: plain-text` came back as a block whose uuid was `plain-text`,
  so a write under that key would have replaced the identity every `((ref))`
  to the block depends on. Bytes that are not valid UTF-8 are refused too;
  they used to be written and then crash the confirmation. An empty value stays
  allowed: measured, `type::` is written and read back as `""` by both sides,
  and it was the key, not the value, that broke the page above.

  `remove-property` addresses the key the same way. Without that, the fix would
  have opened a gap it did not have before: `set --key Status` now stores
  `status`, and `remove --key Status` would have reported success while removing
  nothing. A key `set-property` refuses is passed through unchanged, since
  earlier versions stored such keys verbatim and the database can still hold
  one until the next re-index.
  See [#21](https://github.com/muellerei/logseq-cli/issues/21).

- `add-note-content` and `insert-block` printed a refused `--property` to
  stdout under `--json`, where only payload belongs. A caller parsing stdout
  got an error object in place of the result it expected. Both now report
  through `fail()`, on stderr, like the other refusals in these commands. Found while
  fixing the entry above: the new key check went through the same branch.

### Changed

- `_MUTATING_METHODS` no longer lists `logseq.Editor.setBlockProperty` and
  `logseq.Editor.replaceText`. Neither has a wrapper and neither was ever sent:
  all 19 `call()` invocations pass a literal method name, so no input could
  reach them. They date from the initial import and described a tool that does
  not exist.

  The entries are the smaller half. The find is the check that was missing:
  `_CACHEABLE_METHODS` has been held to its call sites since the read cache
  shipped, and the mutating list had no counterpart, which is why two entries
  survived there for the life of the project. Both lists are now bound to the
  wrappers that send them, in both directions.

  No behaviour changes for any command. A method in neither list is read from
  the network every time and leaves the cache untouched, and these two were in
  no code path to begin with.

- The commands moved out of `cli.py` into `logseq_cli/commands/`, one module per
  group of commands, with the click group in `group.py`, the result and error
  helpers in `output.py` and the block rendering in `render.py`. `cli.py` is now
  the entry point that imports them: 5390 lines to 33.

  Nothing about using the tool changes. The console entry point is unchanged,
  every command keeps its name, its options, its defaults and its help text —
  the per-command `--help` output of all 38 command names was captured before
  the first commit and diffed against after every one of them, and it never
  differed. The commands themselves were moved as text, in one commit per
  module, with the suite green at each.

  Two changes are not pure moves and are called out because they are the ones
  that could behave differently. Nine helpers that are read from more than one
  module lost their leading underscore, in a commit where nothing else happens.
  And `handle_connection_error` now builds its wrapper with `functools.wraps`
  instead of copying two attributes by hand, so a callback still names the
  module it came from — without that, the scan that holds "under `--dry-run`
  nothing mutating goes out" across 18 commands would have found nothing at
  all and said so by passing.

- Every numeric option now states its lower bound in `--help`, including what
  `0` means there, because it differs and the difference was written down
  nowhere. `0` lifts the cap for `get-backlinks --limit` and `get-todos
  --refs-limit`. Everywhere else it is refused, and three of those refusals
  were decided by measuring rather than by assuming: `analyze-graph --days 0`
  puts the cutoff at this moment and can only report pages edited in the
  future; `init --days 0` still writes a config, built on no journals and
  announced as "No journals found — is the right graph open?", which blames the
  graph for what the flag did; `suggest-connections --max-suggestions 0`
  returned an empty list under the same kind of misleading message. `find-block
  --limit` and `get-journal-range --tail/--limit` refuse zero as before. `find-knowledge-gaps --min-refs` and
  `suggest-connections --min-shared` are thresholds rather than caps and keep
  taking any value.

  The convention is recorded under *Design Principles* in `CONTRIBUTING.md`, and
  `tests/test_numeric_option_bounds.py` derives the option list from the command
  registry rather than naming them, so a numeric option added later is covered
  the moment it exists. The two thresholds are named exceptions, checked in both
  directions: an exemption for an option that no longer exists fails the suite
  rather than silently covering a future option that inherits the name.

- `CONTRIBUTING.md` says how work here is actually done, in the places where
  following the old wording would not have prevented the mistakes that were
  made. A test has to be shown to fail before it is trusted: one written to
  prove that `search-pages` matches on `originalName` would have passed while
  testing nothing, because the obvious query string survives `.lower()` in
  `name` as well — the fixture uses `Q&A / Support` instead, whose ampersand
  does not survive being slugged. Tests that write to a live graph are to use
  `zz-probe-<timestamp>` pages and delete them. And "update documentation" is a
  four-item checklist now, `--help` included, because both flags in 0.10.0 went
  out without their README row and `AGENTS.md` entry. References name symbols
  rather than line numbers — a comment pointing at `helpers.py:855` outlived
  its meaning within two commits.

## [0.13.0] - 2026-09-16

### Changed

- `get-todos --from/--to` now finds a task on every journal it stands in, not
  only on the page its block lives on. A task carried forward by a
  `((block-ref))` was invisible to any date range: `--from 2026-09-14 --to
  2026-09-16` returned nothing on a graph where three tasks stood in exactly
  those journals. Carrying an open task forward by reference is the ordinary
  way to work in Logseq — the block exists once, every later occurrence is a
  reference to it — so the answer was not merely incomplete, it was empty, and
  an empty result looks plausible.

  The fix reads the `:block/refs` relation, which is a real relation and needs
  no string matching on the `((uuid))` form. One extra query for the whole
  command, roughly 0.17s against a graph with 256 tasks. A task stays **one**
  row: `page` and `uuid` still name the original block, and the days it was
  carried into are added as `references`. Measured on that graph, a task is
  referenced a median of 2 times and one of them 33 times, which is why it is
  an array and why it is capped.

  `--refs-limit` (default 10) caps the list per task and the remainder is
  reported as `references_withheld`, the same bargain `get-backlinks --limit`
  and `find-block --limit` already make — one heavily carried task must not
  decide the size of the output, and trimming must not hide that a task has
  been carried for months. The default is 10 rather than the 3 used by
  `get-backlinks` because an entry here is a date, not a block of text, and
  because the measured distribution breaks there: a cap of 3 trims 12 of 58
  carried tasks, a cap of 10 trims 4. `--refs-limit 0` keeps all of them.
  `--no-follow-refs` restores the old reading, for callers who want to know
  where blocks live rather than where they appear, and skips the read rather
  than fetching what it will not use.

  This is a **breaking** change in the sense that matters: a range query can
  now return more tasks than before, up to 58 more on the measured graph.
  Nothing was removed, and `page`/`uuid` are unchanged.

  A reference on a page carrying no `journal-day` falls out of a range, the
  same rule the origin page has followed since 0.11.0 — 44 of 248 reference
  occurrences sit on ordinary pages, and letting them through would have
  reopened the silent gap that decision closed. See [#15](https://github.com/muellerei/logseq-cli/issues/15).

- A test now holds the README's command tables to the command registry. The
  twenty missing options below were not the defect — they were the symptom. The
  defect is that a table is a hand-maintained view of something derivable, and
  nothing recomputed it: `--help` is generated by Click and stays complete, so
  the gap never hurt enough to be noticed, and every later check *read* the
  table, which looks complete when you read it.

  The test compares instead: every option in the registry must appear in the
  README in one of its forms, every command must have a row, and the section
  counters must sum to the number of commands. It found one more defect on its
  first run — `### Edit` claimed 11 where there are 8 commands, because
  `insert-block` occupies five rows. Corrected.

  Same shape as the `--dry-run` coverage test added earlier in this release,
  and for the same reason: the source is the registry, the document is a view,
  and a view must not be able to disagree with its source.

- The README documented 20 options that the CLI accepts but never named —
  among them `--min-refs`, `--min-shared`, `--upsert-heading`, `--no-backlinks`
  and the `--date` of the three journal writers. Some of them decide what a
  command returns: `suggest-connections --min-shared` (default 3) is the filter
  that determines whether a pair is considered at all, and a reader who cannot
  see it has no way to tell why a result is empty.

  Found by checking every option in the command registry against the README
  instead of reading the tables, which is how they stayed invisible: a table
  looks complete when you read it, and only a comparison shows what is not in
  it. The gap predates this release — `--min-refs` was already undocumented in
  0.9.0.

  Boolean options are listed in the form a caller actually types: `--no-create`,
  `--multi-block`, `--no-preserve`. Writing the default-on form would have
  documented a flag nobody passes. `--no-preserve` in particular is not the
  `--no-preserve-formatting` one would guess from its positive form.

- `CONTRIBUTING.md` said `cli.py` was "~4000 lines". It was 4771 when that
  sentence was written and is over five thousand now, so the number was never
  right and drifted further with every release. Replaced with a statement that
  does not go stale and names the consequence instead of a count — a figure
  maintained by hand is the same defect this project documents elsewhere.

### Added

- `examples/carried-over-todos.sh` lists the tasks standing in the last N days,
  longest-carried first, and says for each how many journals it has been taken
  along and how many of those fall inside the window. That reading only became
  possible with the block-ref work above: before it, a task's date was the day
  it was first written down, so "how long have I been moving this?" had no
  answer in the payload.

  Uses `--refs-limit 0` for the count, which lifts the per-task cap without
  widening the window — occurrences before the range stay in
  `references_withheld`, and the sum of both is what makes the total a
  duration rather than a visible fraction.

### Fixed

- `examples/weekly-todos.sh` counted `data.get('tasks', [])`, a key
  `get-todos --json` has never emitted — the payload has carried `todos` since
  the initial import. The `.get` default swallowed it: the script reported
  "Total: 0 open tasks" against any graph and printed an empty per-page
  breakdown under it, which reads as a quiet week rather than as a broken
  example. It now reads `data['todos']`, so a future rename fails loudly
  instead of counting zero.

  Two tests hold both halves — the example may only read keys the payload
  carries, and the payload keeps carrying them. Found while checking the
  block-ref work above for consistency against the rest of the repo, not by
  running the example, which is the part worth noting: an example nobody runs
  is documentation that can disagree with its source.

## [0.12.0] - 2026-09-15

### Fixed

- `get-todos --from/--to` only filtered the journal subset of the result. A
  task whose page carries no `journal-day` was admitted regardless of the
  range, so a range that predates the graph still returned every task on an
  ordinary page — most of the result, silently unfiltered. The `--help` text
  said as much ("Non-journal pages are always included"), which made the
  behaviour documented rather than defensible: no caller could tell which part
  of the output had been filtered and which had been waved through.

  A task that cannot be shown to fall inside the range now falls out of it.
  The same applies to an unparseable `journal-day`, which took the exception
  branch and was likewise let through — a rarer input reaching the same silent
  pass-through.

  This changes output for anyone passing `--from` or `--to`. Without a range
  nothing changes, and the options were absent from the README, so the fix was
  preferred over a second flag guarding the old behaviour.

- `create-page` reported success for a page that already existed. Logseq
  answers createPage for an existing page with that page rather than an error,
  so the command could not tell "created" from "was already there" — and said
  `created` either way, with exit 0. `--content` then appended to the page that
  was already there, so an agent retrying after a timeout duplicated content
  and was told the write had succeeded.

  The page is now looked up first and an existing one is refused, naming
  `add-note-content` as the way to add to a page that is there. This is the
  same class of defect as the silent write failures closed in 0.6.0: an
  operation that could not have worked, reported as though it had. The comment
  beside the content write already named the class for `--content`; the page
  itself had been left out.

### Added

- `get-todos --due-from/--due-to` filter by when a task is due, from
  `SCHEDULED`/`DEADLINE`, as opposed to `--from/--to`, which date a task by the
  journal page it sits on. Both dates are surfaced per task; a task carrying
  both is placed by its deadline, since that is the commitment.

  **Repeating tasks needed a decision.** Logseq stores the date as written and
  never the next occurrence — a weekly task created in 2020 still reads
  `20200106` — so filtering on the stored value would place a live task in the
  year it was created. The next occurrence is derived instead, and reported as
  `next_due` beside the stored date rather than replacing it.

  The interval grammar (`+`, `++`, `.+`) and the weekday rule for week repeats
  are Logseq's own, read off `frontend/handler/repeated.cljs` (0.10.12). The
  starting point deliberately is not: `next-timestamp-text` runs when a task is
  ticked off (`update-timestamps-content!` in `handler/editor.cljs`), where the
  stored date is near today and a single step suffices. Applied to a task that
  was never ticked off, `+` and `++` return a date still in the past, which
  answers nothing about what is due. So the single step is kept where it lands
  in the future, and otherwise the `.+` loop runs for every form.

  An initial version excluded repeaters from the range and reported them, on
  the assumption that `.+` needed the completion time and could not be derived.
  Reading the source refuted that — all three forms compute from the written
  date, the clock and the interval — so the weaker answer was replaced. What
  survives of it: a repeater whose interval cannot be read gets no `next_due`,
  and is reported on stderr rather than guessed at.

  Also fixed while here: the task text no longer carries the `SCHEDULED:`/
  `DEADLINE:` lines or the `:LOGBOOK:` drawer. Those are metadata of the task,
  not the task, and left in they made a reported repeater print its own
  timestamp line instead of what it says.

- `get-backlinks --with-context` shows the blocks that do the linking, not
  only the page names. `getPageLinkedReferences` already answers
  `[page, [block, ...]]` pairs, so the blocks arrive with the call that yields
  the names — a caller who wanted to know *why* a page links back was fetching
  and searching each page again for a read that had already been paid for.

  Behind a flag because the plain listing is a pinned shape, and because a page
  mentioned fifty times would otherwise decide the size of the output.
  `--limit` (default 3) caps the blocks per linking page and reports the
  remainder as `withheld`, the same bargain the other reads make. A properties
  block is skipped: it is the linking page's own metadata and holds no mention.

- `get-page --resolve-refs` names the block refs whose target is gone. The
  detection already existed and was discarded: a failed lookup falls back to
  printing the raw `((uuid))`, which is exactly how an unresolved ref renders —
  so the output held two different things spelled the same way, and nothing
  said which was which. The uuids are now collected during resolution and
  reported on stderr, with `dead_refs` in the JSON payload.

  A notice, not an error, and only under `--resolve-refs`: without the flag
  nothing is looked up, so no claim about liveness could be made. Measured at
  0 dead refs across 821 distinct refs in the reference graph — this is not a
  defect there, it is cheap because the detection was already being thrown
  away, and graphs with more deletion history are the case it serves.

- `--content-file -` reads stdin, so content that is already in a pipe no
  longer needs a temporary file first — the one detour the option exists to
  remove. It goes through `read_content_file`, the single place both
  `--content-file` and `--tree-file` pass, so all of them gained it at once.

  A file literally named `-` becomes unreachable through this flag. That is the
  usual trade for the convention, and `./-` still names the file.

- `--dry-run` on `create-page` and `add-journal-entry`, the last two writes
  without one. The README has promised "`--dry-run` on everything that writes"
  since 0.9.0, and nothing held it to that: every dry-run test named the
  commands it checked, so a command that was never named was never missed.

  A test now walks the command registry instead, marking a command as writing
  if its body calls a mutating API wrapper and failing if it has no `--dry-run`.
  That is read off the module source rather than a list kept by hand, so the
  two cannot drift apart — a new write command is covered the moment it is
  added.

  Worth recording, because it is the same mistake one layer up: the first
  version of `add-journal-entry --dry-run` previewed *after* creating the
  journal page, so the one run meant to change nothing left a page behind. The
  registry test does not catch that — it only sees the flag exists — so the
  assertion that a preview issues no mutating call is spelled out separately.

## [0.11.0] - 2026-09-15

### Security

- `edn_string` let most control characters through unescaped. Only `\n`, `\r`
  and `\t` had short forms; the other twenty-nine in the C0 range, and DEL,
  travelled into the query as raw bytes — while the function's own docstring
  already claimed that "control characters become EDN escapes". They now leave
  as `\uXXXX`, the three familiar ones keeping their short form so a query a
  human may read does not spell the common case the long way.

  Not a way out of the string literal: that still needs a quote or a newline,
  and both were already covered, so nothing could be injected through this.
  What it fixes is the same class of defect as the escaping gap in 0.9.0 — a
  value that does not arrive as it was meant, and a stated rule that the code
  did not keep. Found by re-reading the upstream project whose hardening
  prompted the 0.9.0 work (`kerim/logseq-http-server` 0.0.7), which escapes
  control characters as a group; three of its four hardening items were
  already covered here, this one was not.

  The test walks the whole C0 range plus DEL rather than the few that seemed
  likely — "likely" is what left the gap, since the three with familiar names
  were handled and the rest were not.

### Added

- `doctor` now names which Logseq generation is on the other end. A 2.x (DB)
  graph answers this same HTTP API, so every existing check passed against one:
  port open, token accepted, API responding. What it does not carry are the
  fields these commands read — 2.x renamed `:block/original-name` and
  `:block/content` to `:block/title` — so reads came back empty instead of
  failing, which is the shape an empty graph has. The user was left comparing
  their own notes against a result that could not tell them the cause was one
  version number away.

  The rule is Logseq's own: a graph url starting `logseq_db_` is a DB graph,
  `logseq_local_` a file graph (`db-based-graph?` in
  `deps/db/src/logseq/db/sqlite/util.cljs`, prefixes in
  `deps/common/src/logseq/common/config.cljs`). Taken from upstream rather than
  inferred from a response, so the classification rests on the definition both
  kinds are built from instead of on one observed example.

  Two candidate signals were rejected by measuring rather than reasoning:
  `file` is set on 962 of 1845 pages and `format` on 22, so neither separates
  the kinds. Two API routes were rejected by reading upstream:
  `checkCurrentIsDbGraph` exists in 2.x but not in 0.10.15
  (`MethodNotExist`), and `getInfo().supportDb` reads like the flag for this
  while being hardcoded `true` — it says the build can open DB graphs, not
  that this graph is one.

  An unrecognised or absent url reports as undetermined and leaves the run
  healthy. A wrong "file graph, all good" would be worse than no answer: it
  rules out the one cause the reader should be looking at.

### Fixed

- The port was never checked. `LOGSEQ_PORT=nonsens` went straight into the
  URL, and the run came back with `port: 127.0.0.1:nonsens no listener` —
  which is the same sentence a correct port gets when Logseq is simply not
  running. Two causes, one message, and the one people act on is the wrong
  one: they go looking at Logseq's HTTP settings for a typo that sits in their
  shell profile. It is now rejected before the first request, naming the
  offending value and the range. The message names *where the value came
  from*, `--port` or `LOGSEQ_PORT`, because that is the thing the reader has
  to go and change; pointing at the environment variable for a value passed
  as a flag sends them to a setting that is not the one in effect.

  Two deliberate limits. Surrounding whitespace is stripped rather than
  rejected — a trailing newline is what a shell pipeline leaves behind, and
  the value is usable once it is gone. And the check only runs when the port
  is actually used: `LOGSEQ_API_URL` replaces the assembled URL, so a stale
  `LOGSEQ_PORT` in a profile must not fail a run that never reads it.

  Found by re-reading a comparable project (`wolf-jonathan/logseq-cli`), which
  hardened the same spot. Of its hardening items, this was the only one not
  already covered here: the `KeyError: 'originalName'` from its issue #1 (and
  the missing `uuid` beside it) cannot occur here — all nineteen reads use
  `.get()` with a fallback, and the two direct `["uuid"]` accesses each sit
  behind a check — its GET-based connectivity probe has no counterpart because
  this client speaks POST throughout, and host/port were already configurable.
  `LOGSEQ_CLI_CACHE_TTL` two lines below had carried this same guard since it
  was introduced; the port had not.

- `get-page` did not report unresolved block references. Without
  `--resolve-refs` the output keeps every `((uuid))` verbatim, which carries no
  meaning for a reader that is not the Logseq app; `get-journal-range` has
  counted them on stderr since the flag existed, but `get-page` stayed silent,
  so the same page read through two commands gave two different answers about
  whether the output was complete. It now emits the same count. stdout is
  unchanged, `--json` stays parseable, and a page without references prints
  nothing extra.

- `query-pages-by-property` found only the pages whose value happens to be
  stored as a scalar. Logseq keeps a property value either as a plain value or
  inside a collection, and the page does not show which: on a real graph `team`
  was `"Core"` on two pages and `["Core"]` on ten others, and the query
  compared with equality, so it reported one match where eleven existed and
  said nothing about the rest. 592 of that graph's property values are
  collections — `alias` (228), `tags` (141), `team` (46), `role` (14) — so this
  is not an edge case of one unusual key; the same key holds both shapes in one
  graph. The value clause now covers both forms. `coll?` and `set` are not
  available as datalog predicates here, so the two shapes are tried side by
  side rather than normalised first.
  `smart-query`'s person lookup carried the same construction. It was not
  failing, because `person_property` pointed at a scalar-valued key — but that
  setting is configurable, and aimed at a list-valued one it would have
  returned too few just as quietly. Fixed alongside rather than left as a
  known latent defect.
  The listing also printed a collection as Python's repr (`team:: ['Core']`);
  it now reads as the page spells it (`team:: Core`, and
  `tags:: git, Monorepo, Multiapps` for several values). `get-properties` was
  not affected — it prefers Logseq's own text values.

### Changed

- The read cache no longer lists `logseq.Editor.getPageProperties` as a
  cacheable method. It was in that set from the initial commit and never called
  once: the method is declared in Logseq's plugin API, which is presumably
  where the list was first copied from, but the HTTP server does not expose it
  and answers `MethodNotExist: get_page_properties` (checked against 0.10.15,
  in three argument forms, against a page `getPage` resolves fine). Page
  properties are read through `get_page` plus the first block instead, which is
  what 0.6.0 describes. Nothing changes at runtime — an entry for a call that
  never happens costs nothing — but the set is read to learn which reads the
  tool makes, and it was making a claim that was not true. A test now holds
  every remaining entry to a call site in `api.py`.

## [0.10.0] - 2026-09-14

### Added

- `find-block --limit N`. A word that recurs across months of notes matches
  thousands of blocks, and every one of them was printed: 1382 matches came to
  220,049 characters of text, 556,888 as JSON — more than the 30-day journal
  range the README uses as its example of unbounded output, against a response
  cap of roughly 25,000 tokens. The command had `--first` or nothing in
  between. The cut cannot move into the query, because DataScript ignores a
  `:limit` clause and hands back the whole result set either way (measured:
  71 ms and 473 KB for those 1382 matches), so it happens after the read and
  what was withheld is always named on stderr —
  `showing 10 of 1382 match(es) ... 1372 omitted`. stdout stays pure payload in
  both output forms. `--first` now carries the same notice: it used to drop the
  rest in silence, so a caller could not tell an unambiguous hit from one of
  hundreds.

### Fixed

- `insert-block --tree` dropped every `id::` property in the tree and reported
  success. An `id::` names the UUID a block is meant to keep; Logseq only
  honours it when the write asks for it, so the blocks landed under fresh UUIDs
  and every `((uuid))` elsewhere in the graph that pointed at the originals was
  left dangling — damage outside the page that was written, which Logseq then
  writes back as plain text. The existing verification could not see it: it
  counts the new blocks, and the count was right; only the ids were not the
  ones asked for. Both write paths were affected, the batch and the per-block
  one, so a single-block tree lost its id just the same.
  Keeping the ids unconditionally would trade one silent fault for another: an
  outline copied while the original still exists would put the same UUID on two
  blocks and make `((ref))` ambiguous. So the ids are kept only on request, via
  `--keep-ids`, and their loss is never silent again — without the flag the
  command says on stderr how many were dropped. With the flag, ids that are not
  RFC 4122 UUIDs abort the command before anything is written rather than being
  ignored. `--keep-ids` cannot preserve ids on top-level blocks inserted with
  `--page X --top-level`, because the page-append API takes no UUID; the
  command says so instead of half-working.

## [0.9.0] - 2026-09-14

### Security

- Values entering datalog queries were interpolated via f-string: one call
  site half-escaped (quote but not backslash), the rest not at all, so a
  crafted page name or content string could alter the query. A new build
  layer (`logseq_cli/datalog.py`) provides `edn_string` (backslash-then-quote,
  closes the trailing-backslash bypass), `edn_keyword` (whitelist, rejects
  injection shapes) and `page_name_literal` (lowercases, since `:block/name`
  is stored lowercased). All interpolating call sites go through it.
  `smart-query --advanced` stays the documented raw pass-through.

### Added

- A configuration file, for the handful of things that describe *your* graph
  rather than Logseq: the namespace holding your project pages, the property
  marking a person page, the heading journal writes go under, and the words
  `analyze-journal-patterns` scores. These were literals in the source, taken
  from the graph this CLI was written against — `smart-query --request
  "projects"` searched `projekte/` for everyone. They now have no built-in
  default at all: a command that needs one and does not find it names the
  setting and exits 1, rather than returning the empty list that is
  indistinguishable from "you have no projects". Read from
  `LOGSEQ_CLI_CONFIG`, else `$XDG_CONFIG_HOME/logseq-cli/config.toml`, else
  `~/.logseq-cli.toml`; flags and environment variables still win over it.
  `[journal.headings]` adds short names, so `--under-heading tasks` can stand
  for whatever that section is called in your graph, while an unlisted name is
  passed through unchanged so literal headings keep working. Three commented
  example files and `docs/configuration.md`, which covers what to put in when
  a section does not exist in your graph and how to read the right values out
  of it.
- `logseq-cli init` writes that file for you, from the graph itself: the
  headings your recent journals use, the namespace most pages sit under, the
  most common `type::` value. Every suggestion carries the count it rests on,
  and where counting cannot decide — two sections in every journal, two
  namespaces of equal size — the alternatives are named in a comment instead
  of one being picked by insertion order and presented as a finding. It reads
  only the most recent journals, so a section abandoned years ago cannot
  outrank the one in daily use, and it will not overwrite an existing config
  without `--force`.
- `doctor` now checks the runtime before the connection: the Python version,
  whether `click`, `requests` and a TOML parser import, and which config file
  is in effect. A broken install otherwise surfaces later as something
  unrelated.

- `--dry-run` on the seven write commands that lacked it: `set-todo-status`,
  `set-property`, `remove-property`, `set-block-property`, `add-block-ref`,
  `add-note-content` and `rename-page`. It was previously only on the writes
  that cascade, which left the in-place ones — the ones that overwrite without
  a trace — with no way to look first. Each preview reports the state that
  would be replaced: the old marker, the property value about to be
  overwritten (or that the key is not set at all, which the live call cannot
  distinguish from a successful removal), the target page and heading, and for
  `rename-page` the pages whose `[[links]]` Logseq would rewrite graph-wide.
  Two previews catch errors the write path cannot detect: `set-block-property`
  fails on an unknown UUID, and `add-block-ref` warns when the source block is
  missing, which would otherwise write a ref that renders as nothing — both at
  the cost of one extra read taken only on the `--dry-run` path. Every
  validation still runs under `--dry-run`, and no preview creates the page or
  heading it reports.

### Fixed

- The writing commands accepted empty `--content` and wrote a blank block,
  reporting success. `--content "$(cat file)"` collapses to an empty string
  when the file is missing: the shell reports that on stderr but still exits
  0, so three empty blocks reached a journal under an `Inserted block ...`
  confirmation for each. `--content-file` had refused empty input since it was
  added; `--content`, where a failed substitution is more likely, had no such
  check. `insert-block`, `update-block`, `add-journal-block` (each value of
  the repeatable form) and `add-journal-content` now reject content that is
  empty or only whitespace, before any API call and before `--dry-run` prints
  a plan. For `update-block` the blank value did not add a block but erased
  the text of an existing one.

- The analysis commands reported numbers that looked like measurements but
  were not, which is worse than an obvious failure because a plausible number
  gets believed. Found by judging their output against a real graph rather
  than asserting that output exists:
  - `analyze-graph` counted "todo" anywhere and case-insensitively, so
    "Todo-Liste" in prose and the `TODO` inside a DONE block's logbook line
    counted as open tasks. It reported 438 for a graph with 256. The checkbox
    half of the same pattern kept that flaw one round longer: the markers were
    anchored to the start of a block but a bare `[ ]` still matched anywhere,
    so `tags = [ ]` in a code snippet, an empty markdown link and a table cell
    each counted as an open task — a graph with no tasks at all reported three.
    A checkbox is now `- [ ]` at the start of a block, which is what
    `analyze-journal-patterns` had required all along; the two counters measure
    the same thing and now agree.
  - The mood counters read negations backwards: "nicht zufrieden" and "not
    happy" both scored positive, 16% of positive hits in one 90-day sample.
    Free word counting is gone; a line now has to state a mood (`mood: good`,
    `stimmung: mies`, labels from config) and the word lists classify that
    value. The evidence lines follow the same rule, so they can no longer
    contradict the count above them.
  - `suggest-connections` ranked coincidence above substance: two pages
    linking the same single page scored 1.0 under Jaccard and outranked a pair
    sharing 35 topics out of 38, while `--min-confidence` then removed the good
    pair and kept the coincidences. A single shared topic no longer counts
    (`--min-shared`, default 3) and ties break on the number of shared topics.
  - `find-knowledge-gaps` reported Logseq's own by-products as findings —
    `#272` in a sentence becomes a page named "272" — 596 orphans in one graph,
    almost all of that kind, burying the real ones. Names that are too short,
    carry no letter, start or end with stray punctuation, or spell a date in
    file-name form are no longer counted. "Underdeveloped" also skips a page
    whose namespaced namesake has real content: an empty `Alpha` next to a
    written `projects/Alpha` is an anchor for the name, not a gap.
- `project_tags` only ever matched the tag form, so a graph writing
  `[[Alpha]]` rather than `#Alpha` — the common case — configured the setting
  and saw no change. Both spellings count now.
- A project written as `[[projects/alpha]]` was counted under the name `null`,
  because the pattern has one group per spelling and the caller read group 1.

- Rejected queries looked like empty results. Logseq answers a broken query
  with HTTP 200 and `{"error": ...}` in the body, so a query that never ran
  reported zero hits with exit 0, and the error payload was even cached for
  60 seconds. `datascript_query` now raises `DatalogQueryError`, the cache
  no longer stores error payloads, and the error decorator reports the
  reason (`datalog_query_failed` / `invalid_property_key`) with a non-zero
  exit, honoring `--json`.

- `smart-query`'s content-search fallback caught every exception and
  silently switched to a page-name search with exit 0, turning a connection
  drop or rejected query into plausible hits for a different question. The
  fallback now keys off an empty result, not an exception; real errors
  surface through the decorator.

- `query-pages-by-property` found nothing when the key was typed as Logseq
  displays it: display uses camelCase (`excludeFromGraphView`), datalog
  stores kebab-case (`exclude-from-graph-view`); 9 of 35 keys in the
  reference graph were affected. The query and the value lookup now match
  both spellings, so either form returns the same pages.

- A fresh `pip install -e` aborted with a flat-layout error once `local/`
  appeared as a second top-level directory: setuptools auto-discovery saw two
  packages and refused. The package list is now explicit in `pyproject.toml`.

- `get-properties --property` failed for every multi-word key, in both
  spellings: the API returns camelCase keys (`excludeFromGraphView`), and the
  lookup lowercased the typed key into a form matching neither camelCase nor
  kebab-case. The lookup now compares keys with dashes stripped and case
  folded, so camelCase, kebab-case and all-lowercase all find the stored key;
  the stored spelling is reported back.

- `replace-text` ran the find/replace pattern over a block's whole content,
  including its verbatim property lines (`id:: <uuid>`, `key:: value`). A
  `--find` matching inside an `id::` line rewrote it, breaking every
  `((block-ref))` to that block, irreversibly. Replacement now runs line by
  line and leaves property lines untouched; editing a property value remains
  the job of `set-property`.

- Hierarchical insertion (`--tree`, `add-journal-content`, pasted outlines)
  turned a property line such as `collapsed:: true` or `id:: ...` into a
  standalone content block: the outline gained a bogus block and the
  property never reached its parent. Property lines now merge into the
  preceding block, matching Logseq's own semantics.
- `doctor` crashed with a raw traceback when a section was written as a flat
  key — `graph = "projects/"` instead of `[graph]`, which is valid TOML and an
  easy typo. It reached into the section with `.get()`, and a string has none.
  The one command whose job is to diagnose a broken config was the one that
  fell over on it, and `--json` could not turn the crash into an error object
  either. It now reads sections through the same accessor as the rest of the
  code, which has carried the `isinstance` guard all along.
- `delete-page` reported `0 block(s)` for a page whose block tree could not be
  read: the failure was swallowed into an empty list. Zero is the one number
  that makes a full page look safe to drop, and the same count feeds the
  interactive confirmation prompt — so the reassuring value appeared exactly
  where the decision is made. A failed read now stops `--dry-run` and the
  prompt with a message naming the page, instead of describing it with a
  number nobody measured. `--force` still deletes (there the count is output,
  not a gate) but reports the size as `unknown`. A genuinely empty page keeps
  its `0`: that is a fact, not a failed read.

### Removed

- The legacy tree inserters `insert_formatted_content` and
  `insert_block_tree` accepted a failed write (HTTP 200 + `null`) as
  success. No command called them anymore; all insert paths use the strict
  variants that abort on a silent write failure.

### Changed

- Every user-facing string is English now. The guard messages for multiline
  `--content` and the `--content-file`/`--no-preserve` conflict were German
  in an otherwise English CLI, and they fire on a common mistake, so they
  were among the messages users saw most often.
- Example page names in `--help` output no longer come from the graph the
  CLI was developed against. They are Alice/Bob/Carol now.
- The test suite runs in CI on Python 3.10 through 3.13. `pytest` is
  installable from the repo as the `dev` extra: `pip install -e ".[dev]"`.
- `AGENTS.md` passed the token as `--token` in all 34 examples. Command-line
  arguments are visible to any process via `ps` and land in the shell
  history, so the documented path is `LOGSEQ_TOKEN` in the environment now;
  `--token` stays in the setup check, where nothing is exported yet, and is
  documented once as the override.
- The README opening named features rather than saying what the tool is for
  or who drives it, and a new "Design notes" section records four decisions
  that shaped it — verified writes, escaping in one place, analysis measured
  against a real graph, bounded output — each linking to the release it came
  from.
- The MIT copyright named "logseq-cli contributors" for a repository with a
  single commit identity, and carried a year range starting before the first
  commit.

## [0.8.0] - 2026-08-28

### Fixed

- `update-block` silently deleted every property of the block it edited.
  Properties live inside the block content (`prio:: 1` as a line of the same
  block), so replacing the text dropped them, even though the command's own
  help says properties belong to `set-property`/`remove-property`. Following
  that rule was not enough: changing the text was the loss. `updateBlock`
  accepts the properties back through its documented third parameter, so they
  are read before the write and carried along; the round trip is lossless
  (`owner:: [[Bob]]` stays a link) and `id::` is unaffected, so block
  references survive. `--dry-run` names what it will keep.

- Transport errors ignored `--json`. A wrong token or a Logseq that is not
  running was reported as prose, so an agent parsing stderr got unparseable
  text at exactly the point where it needed a reason. Both now go through
  `fail()` and carry `reason` (`connection_refused` / `http_error`), plus
  `status_code` and a token hint on 401/403. Without `--json` the wording is
  unchanged.
- `get-block` with an unknown UUID printed `null` on stdout and exited 0,
  which reads as a successful empty block rather than a miss. It now fails
  like `get-page` does: exit 1, error on stderr, `"exists": false`.

- `set-todo-status --content` silently rewrote the first of several matching
  blocks. With two TODOs sharing a text it updated one and reported success,
  and the caller could not tell which or that there had been a choice. It now
  aborts and lists the candidates, like `--where-content` does.
- `copy-block --remove` deleted the source even when the copy never landed.
  The copy path ignored its write results, and Logseq answers a failed write
  with HTTP 200 + null, so a copy that wrote nothing was reported as
  "Moved 1 block(s)" with exit 0 and the original was removed anyway. Every
  insert now goes through `require_insert`, so the source is only removed
  against a copy that is known to exist.

### Added

- `remove-property --id UUID --key K` removes a property from any block. The
  command was wired to the page's first block, so a property on a log entry or
  a TODO could not be removed at all, although the API had supported it all
  along. `--name` (page) and `--id` (block) are mutually exclusive.
- `update-block --where-content TEXT [--page NAME] [--regex]` selects the block
  by text instead of UUID, removing the `UUID=$(find-block ... | python3 -c ...)`
  detour that recorded use is full of. Because the command overwrites content,
  an ambiguous selector aborts and lists the candidates rather than picking one:
  guessing rewrites one of several equally valid blocks with no way to tell which.
- `insert-block --quiet` prints the confirmation line without one uuid line per
  block, for tree writes where only the result matters.
- `find-block --with-children` and the `--where-content` selectors share one
  content lookup (`find_blocks_by_content`), so a query fix cannot land in one
  and miss the other.
- `move-block --id UUID (--under UUID | --before UUID)`: structural move that
  keeps the block's UUID, so `((block-refs))` to it survive - unlike
  `copy-block --remove`, which writes a new block and leaves every ref dead.
  Position follows what `moveBlock` actually does rather than what its option
  names suggest (probed against a live graph): `before: true` makes it the
  sibling in front of the target, everything else nests it as the first child;
  the documented `sibling` option has no effect. `moveBlock` answers null for
  success, for a missing target and for a refusal alike (a block cannot move
  into its own subtree, and Logseq says so only by doing nothing), so each move
  is verified by re-reading and a move that did not take is reported as an error.
- `find-block --with-children` prints each match with its sub-blocks indented.
  Recorded use shows 51 of 65 context-greps were `get-page --heading "## Log" |
  grep -A<n> "14:57"`: a subtree read expressed as a text read with a guessed line
  count, which drags in the following entries when the guess is too high and cuts
  the subtree short when it is too low. The datalog pull carries no children, so
  each subtree costs one extra read; the fan-out is capped at 25 matches and the
  remainder named on stderr rather than silently dropped. Without the flag nothing
  changes: no extra read, preview stays truncated.
- Tree writes now go out as a single `insertBatchBlock` call instead of one
  `insertBlock` per node. Across recorded use that is 2645 round-trips for 217
  multi-block writes, i.e. one call each. `insertBatchBlock` answers `null`
  whether it wrote everything, part of it, or nothing, and a malformed node is
  skipped while its siblings land (verified against a live graph), so the write
  is proven by re-reading the parent's children and counting: a short count
  aborts with the partial state named, as the per-block path did. Set
  `batch=False` on `insert_block_tree_with_uuids` to force the old path.
- `insert-block --child-of UUID --first` inserts at the HEAD of the child list
  instead of appending last. Previously the first-child position was not
  reachable: `--child-of` always appended, and combining it with `--before` was
  rejected as a conflicting target. Works with `--content` and `--tree`; with a
  tree the first root takes the head position and the remaining roots chain as
  siblings behind it, so declaration order is preserved (`insertBlock` has no
  "nth child" option, and looping with `before=true` would reverse the order).
  `--first` without `--child-of` is rejected rather than silently ignored.

### Changed

- `update-block --content` now fails when the text carries newline `- ` bullets.
  The command replaces the content of ONE block and has no tree path, so indented
  sub-bullets silently became raw text *inside* the block instead of children.
  Unlike `insert-block` / `add-journal-block`, the indented form is rejected here
  too, not just the flush one. Changing the line itself -> shorten `--content` to
  that one line; adding children -> `insert-block --child-of UUID`.
- Both guards now share `reject_unsupported_multiline(content, command=...,
  accepts_tree=...)`. Whether a command can write a tree is stated in one place
  instead of being duplicated per command.

## [0.7.0] - 2026-08-08

### Added

- `add-journal-block --content-file FILE` and `insert-block --tree-file FILE`:
  block content from a file instead of `--content`/`--tree`. This solves two
  problems:
  - **No shell quoting.** `--content "$(cat file)"` breaks on an apostrophe in
    the text, and the reflex of escaping every special character mangles
    umlauts along with it. A file path has no shell in between.
  - **Multiple flush `- ` roots are allowed.** Inline, the guard rejects them,
    because there they would silently collapse into ONE block with raw newline
    bullets. From a file, the whole text is parsed as a tree, where flush
    bullets are legitimate sibling roots with children of their own.

  `--content-file` excludes `--content`; `--tree-file` excludes `--tree` and
  `--content`. A missing, empty, unreadable or non-UTF-8 file aborts before
  anything is written.

### Fixed

Four ways the reported block count could diverge from the blocks actually
written. Three of them ended in exit 0 and a success message for text that
never reached the graph. Found while adversarially testing the new file path;
three are older than it.

- **`--upsert-heading` discarded every root but the first** and still reported
  `count_blocks(tree)`. A file with three roots wrote one and reported nine.
  Further roots are now inserted as siblings, and the reported count comes
  from the UUIDs actually returned.
- **The upsert path wrote non-strict** (`insert_block_tree`): a silent write
  failure was skipped and the intended count reported. It now runs through
  `insert_block_tree_with_uuids(strict=True)`.
- **`insert_formatted_content_with_uuids` was the only insert helper without a
  strict contract.** On a page Logseq has not loaded, every append answers
  HTTP 200 + `null`; the `None` UUIDs were counted and reported as
  `Added N block(s)`. Affects `add-journal-block --top-level`, the heading
  fallback, `add-journal-content` and `add-note-content`.
- **`insert_block_tree_at_page_top` restarted its count from 0 per `--content`
  value in the batch path**, so a failure in the second value reported
  `Nothing was written` even though the first was already in place.

Additionally: when a tree insert aborts midway, the message now names the
number of blocks already written instead of `Nothing was written`. There is no
rollback (the API offers none), and the old wording invited a retry that would
have duplicated those blocks.

### Changed

- The guard on `add-journal-block --content` now names `--content-file` as a
  fourth way out. For the inline path it stays exactly as strict as before.
- `--content-file` together with `--no-preserve` aborts instead of silently
  flattening the hierarchy into a single block. `--no-preserve` remains usable
  with `--content` as before.

## [0.6.0] - 2026-08-07

Result of an audit against common CLI conventions (clig.dev, POSIX/grep,
tool design recommendations). All defaults stay unchanged: without the new
flags, every command behaves as before.

### Added

- `--dry-run` for `update-block`, `remove-block`, `copy-block` and
  `delete-page`. These four operations cascade or overwrite (`remove-block`
  takes all children with it, `copy-block --remove` deletes the source), yet
  had no preview until now. `remove-block --dry-run` additionally reports the
  number of descendants that would be deleted along with it.
- Output limiting for the journal read paths:
  `get-journal-range --tail N` (newest N days), `--limit N` (oldest N days)
  and `--heading X` (one section per day only); `get-journal-summary
  --no-content` (drop full texts, keep date/character count/topics).
  `--tail`/`--limit` filter before fetching, so omitted days cost no API call.
  Measured against a real graph: a 30-day range went from 431,996 to 136,289
  characters, a "this week" summary from 143,733 to 793.
- **`doctor`**: read-only health check in a single call. Checks for a listener
  on the API port, the token, a real API response, and whether a graph is
  loaded. It separates the cases that are otherwise tedious to tell apart:
  Logseq is not running / is running but the HTTP API is off / the API answers
  but the token is rejected / API and token are fine but no graph is open. Each
  case gets its own recommended action (`remedy`, in the JSON output too).
  Exit 0 = ready to read and write, 1 = not. Prompted by an incident: the
  Logseq process was running but nothing was listening on port 12315, and
  pinning that down took seven manual diagnostic steps.
- `delete-block` as an alias for `remove-block`. The name is the most common
  wrong guess, because `delete-page` sits right next to it.
- `fail()` helper: with `--json` set, errors are emitted as a JSON object,
  otherwise as plain text. In both cases exclusively on stderr, so stdout stays
  reserved for payload data.

### Fixed

- **Silent write failures are no longer reported as success.**
  `insert_block_tree_with_uuids()` defaulted to `strict=False`: Logseq answers
  a failed insert with HTTP 200 + `null`, so the function stored a `None` UUID,
  skipped that block's children — and the command reported "Added N block(s)"
  with exit 0 although nothing had been written. For a journal entry that means
  the text is gone and nothing says so. Five of eight callers were affected,
  among them `add-journal-block` and `add-note-content` (the sibling function
  `insert_block_tree_as_siblings` already had `strict=True`; the inconsistency
  was unintentional).
  `strict` is now the default, and `insert_block_tree_at_page_top()` verifies
  the top-level append through `require_insert()` as well. `strict=False`
  remains available for callers that deliberately tolerate partial writes.

- **`get-properties` wrongly reported "No properties".** The command read only
  `page_data["properties"]`, but Logseq stores page properties on the first
  block (the property block) when they were written via `set-property` — and
  there the page object stayed empty. The result: intact properties were
  reported as absent, which made `set-property` look as if it had silently
  failed. That exact symptom was recorded in the project notes ("properties
  broken", "only fixable via delete-page and rebuild") — in reality it was a
  read error, not data loss. There is now a fallback to the first block; if the
  page object does return properties, the previous path is used with no extra
  call.

### Changed

- `delete-page` now decides on confirmation via `sys.stdin.isatty()` rather
  than via `--json`. Previously `--json` acted as an implicit force switch, but
  a script may well ask for JSON purely for data processing. Interactively it
  asks; non-interactively `--force` is mandatory (otherwise exit 1). This
  separates output format from safety confirmation.
- `get-page` returns exit 1 when a requested page does not exist (output
  `(page does not exist)`, `"exists": false` in JSON). An existing but empty
  page stays exit 0 with `(empty page)`. Previously the two cases were
  indistinguishable. Batch reads still output every page that does exist and
  report the error only at the end.
- Truncated journal results report on stderr how many days were omitted
  (`showing N of M ... K omitted`). Without truncation, no such notice.

## [0.5.0] - 2026-05-08

### Added

- `--help` epilogs for all 34 commands. Each command's `--help` now ends with
  one or more concrete invocation examples plus tight notes on common
  footguns (e.g. `set-property` vs `update-block`, `--resolve-refs` hint,
  `--dry-run` for `replace-text`, `set-todo-status` as the preferred TODO
  transition, deprecation of `add-journal-entry`). Reduces the LLM/agent
  failure mode of guessing flag syntax from related tools.
- `get-journal-range --from/--to` and `get-todos --from/--to` now accept the
  keywords `today`, `yesterday`, and `tomorrow` in addition to `YYYY-MM-DD`.
  Brings parity with `--date` and `--journal-date`, which already supported
  these keywords via `parse_date_keyword`.

### Fixed

- `get-page --heading` now matches headings tolerantly via
  `normalize_heading()`, so a query for `"## Tasks"` correctly returns
  the section even if the stored block is `"## Tasks {{renderer :todomaster}}"`
  or has extra whitespace. Previously the naive equality check caused
  `--heading` to silently miss any Logseq journal that uses renderer macros.

### Chore

- Untracked 8 `.pyc` bytecode files that were accidentally committed in an
  earlier version. `*.pyc` and `__pycache__/` were already in `.gitignore`;
  the index is now consistent with that rule.

## [0.4.0] - 2026-05-06

### Added

- `get-page --with-ids`: Prefix every block line with its UUID. Output format
  `<uuid>\t<indent-tabs>\t<content>`. Eliminates the `--json | jq` workaround
  for downstream tools that need the UUID alongside the rendered content.
- `insert-block --tree`: Batch-insert a block hierarchy in a single call.
  Accepts either tab-indented text (same shape as `--content`) or a JSON tree
  (`[{"content": "...", "children": [...]}]`). Auto-detected from the first
  non-whitespace character. Combines with `--child-of UUID` (insert under
  block) or `--page NAME --top-level` (insert at page top). Returns UUIDs in
  DFS pre-order. `--content` and `--tree` are mutually exclusive.
- `add-note-content --under-heading "## X"`: Heading-aware insertion for
  non-journal pages. The heading is created if it does not exist; the content
  is inserted as children. Mirrors `add-journal-block --under-heading` for
  the project/topic page case.
- Global `--no-cache` flag: Bypass the in-memory read cache for one
  invocation.

### Changed

- `get-todos` plain-text output now shows the page name inline per task
  (`MARKER [Page] preview`) instead of grouping tasks under a page header.
  JSON output is unchanged — `page` was already part of each task entry.

### Performance

- **In-memory read cache** for the `LogseqAPI` client. Read methods
  (`getPage`, `getBlock`, `getPageBlocksTree`, `getPageProperties`,
  `getPageLinkedReferences`, `getAllPages`, `getUserConfigs`,
  `datascriptQuery`) cache responses for 60 seconds by default. Mutating
  methods (`createPage`, `deletePage`, `renamePage`, `appendBlockInPage`,
  `insertBlock`, `updateBlock`, `removeBlock`, `upsertBlockProperty`,
  `removeBlockProperty`, `setBlockProperty`, `replaceText`) invalidate the
  whole cache. Configurable via `LOGSEQ_CLI_CACHE_TTL` (seconds, `0`
  disables). Bypass per call with `--no-cache`.
- **Parallel fetch in `get-journal-range`**. Journals in the requested range
  are fetched through a `ThreadPoolExecutor` (5 workers default,
  configurable via `LOGSEQ_CLI_RANGE_WORKERS`, range 1–16). Output order
  remains stable (sorted by date). A failure on any single day is embedded
  as an `error` field on that entry; the rest of the range continues to
  process.
- `get-page --resolve-refs` and `get-journal-range --resolve-refs` now
  documented and test-covered. Block references `((uuid))` are resolved
  inline to `<content> ↳ <source-page>`, removing the need for follow-up
  `get-block` calls.

## [0.3.0] - 2026-05-06

Initial baseline. 30 commands across pages,
journals, blocks, search, properties, page management, and graph analysis.
