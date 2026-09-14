# Changelog

All notable changes to `logseq-cli` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
