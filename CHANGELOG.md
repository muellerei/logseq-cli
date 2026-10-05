# Changelog

All notable changes to `logseq-cli` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Entries say what was wrong, how it was found and what the fix cost, so this
file carries most of the reasoning behind the tool.

## [Unreleased]

### Added

- `[graph] keep_empty_blocks_last = true` (or `LOGSEQ_CLI_KEEP_EMPTY_BLOCKS_LAST`)
  makes a write that ends a section go before the empty blocks that end it, so
  they stay last and no empty line is left in front of the entry (#110). Found by
  asking why a journal had an empty line before every entry the CLI wrote: not
  the template, and not the CLI, which writes no empty block (measured with test
  entries, CLI 0.16.0, Logseq 0.10.15), but an empty last block the user keeps as
  a place to click. `insert-block --child-of`, `add-journal-block` and
  `add-note-content` appended behind it and left it standing.
  - With it on, the write goes directly before the first of the empty blocks at
    the end, the other blocks of the same command follow in order, and the empty
    blocks stay last. Nothing is deleted and nothing is written over. A block is
    empty with no text, no properties and no children, so an empty block with an
    empty child is not empty, and empty blocks in the middle of a section are left
    alone.
  - It applies to `insert-block --child-of` and `--page`, `add-note-content`,
    `add-journal-block` (an upsert that adds a block too), `add-journal-content`,
    `add-block-ref`, the root of `copy-block` and a heading that has to be
    created. Not to `--after`, `--before`, `--first` or `add-journal-entry`.
    `--keep-ids` keeps its ids and goes before the empty blocks as it does for
    `--before`. A block of nothing but `key::` lines is not put before the first
    block of a page, where Logseq would read it as the page's properties (`title::`
    renames the page), but appended as without the setting.
  - A first version wrote into the empty block with `updateBlock`. An adversarial
    pass found that a second writer, or the user typing there between the CLI's
    read and its write, would be overwritten without a word, which appending
    never could; the editor gate refuses only an open block. Going before the
    empty block loses nothing and needs no gate: the cursor can stay in the block
    the user clicked (`updateBlock` on it ends in `open_in_editor`, measured), but
    not with `--keep-ids`, which refuses any write while a block is open.
  - `--dry-run` names the block it would go before (`would_go_before_empty_block`)
    and a write that did says `before_empty_block`. Off by default, because an
    empty last block can be a spacer or a template's placeholder: with it off a
    write behaves as it always has, and the existing tests pass unchanged. With it
    on, a write at the end reads the children of its target, not from the cache,
    before and after: the anchor is read a moment before the insert, so the write
    is proven to stand directly before it, and one that landed elsewhere because
    the block moved in between is reported as `write_not_verified`. A
    test finds every function that writes at the end of a section and fails for
    one that ignores the setting.
- `[safety] read_only = true` in the config file, the environment variable
  `LOGSEQ_CLI_READ_ONLY` and the global option `--read-only` switch writes
  off: every command that writes refuses before its first request, reads and
  the editor check included, and so does its `--dry-run`. A preview that says
  "would write" when the real run cannot would lie about it. The refusal is
  `reason: read_only` with `source` (a list of `config`, `env`, `flag`) and
  `config_path`. The switch only tightens: an environment value of `false` or
  a missing option leaves a config that says `true` in force, and there is no
  `--no-read-only`. An unknown environment value switches writes off and
  says so. A test fails when a command that writes is added without the
  check, and `LogseqAPI._post` refuses any write method a second time, so a
  writer the scan misses still stops before the request.
- `[safety]` is checked strictly: an unknown key (`readonly = true`), a
  `[safety]` key outside `[safety]` (top level, another section, a misspelt
  `[saftey]`, nested as `[journal.safety]`) and a value that is not `true` or `false` are errors that name
  the key and suggest the right one. The other sections stay as tolerant as
  they were, and commands that only read run, with a warning where they look at the config.
- `doctor` always shows the state of `read_only` and where it comes from
  (`on (env)`, `off (no [safety] in …)`, `off (no config file found)`, or
  `unknown` with the reason when the config cannot be used), and
  says "Ready to read; writes are off." when it is on. The line does not turn
  a healthy check red: the common failure is a switch that is off without
  anyone noticing, another file or another `HOME`, which a line shown only
  when it is on would not reveal. A section the CLI does not read
  (`[jurnal]`) is named as a note.
- `init` keeps the `[safety]` section of the file it overwrites, and of the
  config in use when it writes a new file at any path the config is looked
  for (`--output ~/.config/logseq-cli/config.toml` with the config in
  `~/.logseq-cli.toml` would hide it; a file behind it takes over once the
  one in front goes), and says so on stderr.
  Refusing would make `init` unusable under `read_only`; dropping it would lift the limit through a command that writes no graph.
  It refuses a file it cannot parse (`reason: config_error`), since it could
  not keep what is in it.

- `docs/safety.md`: what `read_only` protects against and what it does not, how
  binding each way of setting it is, how to keep the config file out of an
  agent's reach, and what to check when it seems not to apply. The README
  gets a short section with the recipe.

### Changed

- `set-todo-status` refuses a block that is no task instead of making it one:
  `no marker` became `DONE no marker`, `todo x` became `DONE x`. Logseq reads
  no marker in such a block (`todo x`, `TODO:`, `TODO` followed directly by a
  line break); the command now stops with `reason: "not_a_task"` and writes
  nothing, also under `--dry-run`. A block that only points to another one is
  refused too, naming `--follow-refs`. With `--content`, blocks that match but
  none of which is a task are refused (`no_task_matches`) instead of one being
  rewritten; with `--follow-refs` this holds for the block at the end of the
  chain, and a match that only points to a task is followed as before. Under
  `--json`, a block not found by `--content`, an ambiguous match and a missing
  selector now answer with a JSON object with a `reason` (`block_not_found`,
  `ambiguous`, `missing_selector`) instead of plain text. Without `--json`,
  these three messages now start with `Error: `, like every other refusal; the
  wording after it is unchanged (#92).

- `get-todos` (`content`, `--match`, `--tag`) now reads an unclosed
  `:LOGBOOK:` as text and a `:LOGBOOK:` inside a code fence as code, the way
  mldoc reads them (measured with mldoc 1.5.7); before, everything after such
  a line was dropped from the task text. A `SCHEDULED:` or `DEADLINE:` line inside a code
  fence and a stray `:END:` now stay too (#92).

- `doctor` no longer advises removing a config file that does not parse:
  "Fix the config file." Once the file carries a limit, removing it lifts the
  limit.

- A command that writes now reads the config file and refuses when it cannot
  be parsed. Most of them never read it, so a broken file went unnoticed. With
  a limit in the file, running on without it would lift the limit.

- A command that writes refuses when `LOGSEQ_CLI_CONFIG` points at a file that
  does not exist (`reason: config_error`, `--dry-run` included). It used to
  warn and go on without a config, which for a read is right and for a write
  lifts every limit the file may hold. Commands that only read keep the
  warning.

- `init` without `--output` writes to the config file that is in use, not to
  the first search path. With the config in `~/.logseq-cli.toml` and nothing
  under `~/.config/logseq-cli/`, it used to create a second file there
  without `--force`; that file is found first, so every setting in the old
  one stopped applying, silently. Now the target exists, so `--force` is
  needed. Reproduced on `main`. Not filed under Fixed because the old
  behaviour was what the help text said.

- AGENTS.md and the agent skill no longer tell an agent to fall back to
  editing the Markdown files when Logseq is not running. It may read them
  then, and only then; it writes nothing and says that Logseq has to run.
  The fallback dated from the first import and sent an agent that ran while
  Logseq was closed, such as a scheduled one, to write exactly where nothing
  checks the write or protects a reference.

- `--resolve-refs` puts what Logseq shows of a Block Ref in its place, on the
  ref's line: the target's first line with its `SCHEDULED:` and `DEADLINE:`
  dates, not its whole text. Logseq 0.10.15 draws a ref as the target's title
  and shows the body only when there is no title (a block that opens with a
  code block, math, a quote, a table, HTML, a rule, a `#+BEGIN_` block or
  properties); such a body is joined into one line. The CLI inlined
  everything. The target's property lines then read as the referencing
  block's own, and so did its `id::` line: an id taken from the output for a
  write edited the target instead (#58). Text after the ref landed behind the
  target's last line, where after a closing fence Logseq drops it from view.
  Properties no longer show, not even for a target that holds nothing else,
  where Logseq shows them. The body is still one `get-block` away. See
  [#98](https://github.com/muellerei/logseq-cli/issues/98).

### Fixed

- A task with a date can be written in one call. `add-note-content --content
  $'TODO x\nSCHEDULED: <2026-09-25 Fri>'` made two blocks: the `SCHEDULED:` line
  became a block of its own and had no effect, and the dry run said "2
  block(s)". Found by reproducing it on a probe page. Logseq's own files keep
  `SCHEDULED:` and `DEADLINE:` lines and a `:LOGBOOK:` drawer with the block
  above, as they keep a `key:: value` line, and the outline parser now does
  too, for every command that reads its text as an outline (`add-note-content`,
  `add-journal-content`, `add-journal-block`, `insert-block` with an indented
  outline, `--tree` given as text). A `:LOGBOOK:` that no `:END:` closes is
  text, not a drawer (measured with Logseq's parser, mldoc 1.5.7), and so is
  one whose `:END:` comes only after a bullet line, so it hides nothing and
  swallows no bullet after it (#92).

- `set-todo-status` no longer breaks a heading task: `## TODO ship it` became
  `DONE ## TODO ship it`, a block Logseq reads no task in, because the command
  took the first word of the text for the marker. It now swaps the marker where
  Logseq reads it, behind the heading prefix (`## DONE ship it`), and
  `get-todos` reports `content` without the marker (`## ship it`). It also keeps
  the space between the marker and a line break: `TODO \nnotes` became
  `DONE\nnotes`, which Logseq no longer reads as a task (measured with Logseq's
  parser, mldoc 1.5.7) (#92).

- A uuid that Logseq holds only as the placeholder of a dead ref (content
  `id:: <uuid>`, no page, kept once Logseq reads the file with the ref
  again) is no block to any command now. `update-block`,
  `set-block-property` and `set-todo-status` sent their write to it, and
  the write proof reported a write that did not show; `get-block` printed
  the placeholder as a block. They now say the block is not found and
  write nothing, as `add-block-ref` and `--resolve-refs` already did for
  it (#70). The rule sits where Logseq's answer is read, so every command
  has it; the three checks that had it each on their own are gone.
  Measured on 0.10.15 along the way: getBlock answers `null` for a page's
  uuid and for a block deleted a moment ago, refs to it or not (#106).

- `set-todo-status --follow-refs` changes the task at the end of a chain of
  refs, never a block on the way. It followed one ref and only when the
  block held nothing else, so it missed the usual case: a block that is
  itself a ref target carries an Id Line, and `((T))` with its `id::` line
  was changed itself into `DONE ((T))`, a second task, while T stayed open.
  A chain H → M → T turned M into that second task, and a block holding
  only `{{embed ((T))}}` got the marker in front of the embed. Now a block
  that holds only `((uuid))` or `{{embed ((uuid))}}`, property lines aside,
  is followed through every such block, the embed with the spaces Logseq
  allows in it (`{{embed  ((uuid)) }}`, measured on 0.10.15); a labelled
  ref `[text](((uuid)))` is a link with text of its own and is changed
  itself, as before. Under
  `--json` the result names the blocks passed in `followed`, and
  `--dry-run` shows them. A chain that comes back to a block it passed is
  refused (`reason: "ref_cycle"`); one that reaches a missing block, where
  it used to write `DONE ((uuid))` into the reference with a warning or
  send the marker to Logseq's placeholder, is refused too
  (`reason: "dead_ref"`). Nothing is written either way. Found in reading
  the code for block refs, reproduced with the test double, the missing
  target measured on 0.10.15 (#106). Cost: one `getBlock` per link, as the
  one step took before.

- `set-todo-status --id` on a block that does not exist answers under
  `--json` with `reason: "block_not_found"` and the `id`; the message is
  the same (#106).

- `get-block` prints the parent block's uuid as `Parent:`, ready for the
  next `--id`, and `Parent: (page)` when the block sits directly under its
  page, so a `Parent:` line in the block's own text does not come first.
  It printed Logseq's internal database id, which no command takes, and
  `Page:` printed that id too under `--no-children`. Found in the output
  of a real `get-block` call. Logseq answers
  both as database ids; the output took `name` or else `id`, and a block
  has no `name`. `Page:` now shows the page's name as Logseq writes it,
  with or without children. Present since the first import; the test
  double answered the parent's uuid there (#104). Cost: one `getBlock`
  for a nested block's parent, and one `getPage` under `--no-children`,
  where the answer does not name the page; a block directly under its
  page with children needs the one request it always did.

- `get-block --no-children` no longer crashes on a block that has children.
  Asked without children, Logseq answers each as a `["uuid", <uuid>]` pair
  rather than a block, and the text output rendered the pairs as blocks:
  `AttributeError` and a traceback, in exactly the case the option exists
  for. Found while checking the `Parent:` fix below and reproduced
  against Logseq. Present since the first import; the test double answered
  `[]` there, so no test ran against Logseq's form. Both doubles now answer
  it, and a shape test holds them to the measured forms (#104). Costs no
  extra request.

- `--resolve-refs` names the page each resolved ref came from, as documented
  (`the actual text ↳ Meeting Notes`). The suffix never showed: asked without
  children, `getBlock` answers the block's page as `{id}` alone (measured,
  0.10.15), and asking with them reads the target's whole subtree. The name
  now comes from `getPage` by that id, with the read cache on once per page.
  The tests answered the page with a name, which Logseq does not, and passed.
- A ref whose block does not exist stays `((uuid))` under `--resolve-refs`,
  and `get-page` reports it as dead, also once Logseq has read the page
  holding it from its file. Logseq then keeps a placeholder `id:: <uuid>`
  without a page (#70), which was inlined as the ref's text.

- The suite would have gone red with no change to the code: ten tests failed
  from 2026-10-11 and one more from 2027-01-01. Their fixtures held a fixed
  journal day (2026-09-10, August 2026) while the commands under test filter
  against "last 30 days" or "this year", so once the day left the window the
  command saw an empty journal and the assertions failed. `journal_day()` in
  `tests/conftest.py` now derives the day from the clock. Reproduced with a
  faked clock on 15 dates through 2028 (month ends, year change, leap day) at
  00:00 and 23:59; all 3265 tests pass on each. No command changed.
  - Two tests looped over the entries of a result and would pass over an empty
    one: `get-journal-summary --no-content`, silently from 2027, and
    `get-journal-range --heading`, on an empty journal at any date. Both now
    assert the list is not empty.
- Sixteen tests passed whether or not the command did anything. They asserted
  exit code 0 and, at most, that something was not written; one searched
  stderr for "dead" where the message reads "no longer exists", so it could
  not fail. Found by making `CliRunner.invoke` a no-op (exit 0, no output):
  135 tests still passed, and for 16 of them no twin test proved that the
  command acted. Each now asserts what exists only if it did: the written
  block, the rendered page, the dry-run notice, the query sent, the JSON
  payload. With the no-op, fourteen of them fail; the other two were mutated
  on their own (forcing `--resolve-refs` on makes the new dead-ref assertion
  fail where the old one passed; `get-todos --from 2099`, which drops every
  task, fails the new one). The empty-graph smoke test now requires exit 0 and
  JSON where it allowed `SystemExit`. No command changed.

- The suite would not have noticed `get-page-stats` losing its `inbound_count`
  field. Its only test looked for the field's absence after a failed read
  (`"inbound_count"` not in stdout), and no test expected it present: renaming
  it left all 3265 tests green. A test now reads the stats when the scan can
  read every page and expects the field and its value.
  - Three more checks asserted that a message is absent after the message had
    gone, so they could not fail: "Could not find or create" (removed with the
    top-level fallback) and "cannot preserve" (removed when top-level blocks
    began to keep their ids), the latter in two tests. The first was redundant
    next to the JSON parse of stderr and is removed; the others now look for
    the dropped-`id::` note, whose wording their sibling tests pin.
  - Found by listing the 154 checks of the form `"text" not in …` and
    asking, for each, whether the code still emits the text and whether any
    test expects it. Checked by mutation: printing the dropped-id note despite
    `--keep-ids` passes the old assertions and fails the new ones in all eight
    cases. No command changed.

## [0.16.0] - 2026-09-26

Every write is now proven, and none overwrites a block you are editing.
Logseq's HTTP API answers `null` to a write whether it happened or not, and
reports a thrown error as HTTP 200; measured on 0.10.15, a write to the
block someone is typing in replaced the editor content at once and dropped
what had not been saved, and `renamePage` onto an existing name merged two
pages without asking. Until now the CLI checked some write paths and took
Logseq's word on the rest. From this release the checks sit in one place,
the API client, where no command can go around them:

- before a write that changes a block, the CLI asks Logseq which block is
  open in the editor and refuses to touch it; inserts no longer take your
  cursor, and created pages no longer turn Logseq's view;
- after each write, it shows that Logseq holds the result, from the answer
  where it carries one and by reading back otherwise;
- a write that is refused or not shown to land fails with exit 1 and a
  `reason` (under `--json` as an error object), and says how many writes of
  the same call landed before it;
- the client refuses an API method it does not know, so a new write cannot
  slip past these checks.

For a caller, exit 0 after a write now means the write is in Logseq's
database. Scripts that matched the old `Error:` text of a failed insert, or
relied on `--under-heading` falling back to the top of the page, need
adjusting (see Changed). The price is one read per write and one editor
check per write that can be refused, about a millisecond each, and a window
of about 0.1 s after a multi-block insert.
See [#99](https://github.com/muellerei/logseq-cli/issues/99).

### Fixed

- A write to the block you are typing in no longer throws away what you
  typed. Logseq replaces the editor's text at once, and everything typed and
  not yet saved was gone; it answered `null`, and the CLI reported success
  (measured by hand, 0.10.15: typing without pause, an `update-block` five
  seconds in, and after Esc only the written text and what was typed after
  it remained). A read right after the write still showed the old text for
  about three seconds, so no check afterwards could have caught it. The CLI
  now asks Logseq which block is open (`checkEditing`) before a write that
  changes a block and refuses with `reason: "open_in_editor"`, naming the
  block and its page: `update-block`, `set-todo-status`, `set-property`,
  `set-block-property`, `remove-property`, `move-block` (the moved block or
  one below it; the anchor is free), `remove-block` (the block or one below
  it),
  `delete-page` (a block of the page) and `rename-page` (a block of the page
  or one that links to it, since Logseq rewrites the link and the open
  editor would save the old text back). An insert does not ask: Logseq
  saves the open block before it inserts. One insert is refused all the
  same: a text with `((X))` while `X` is open and has no `id::` yet, because
  storing the id writes into `X`. The ids are stored only after the
  question, so a refused write leaves no `id::` behind. A block id in
  capitals is compared in lower case, as Logseq answers; compared as typed,
  `update-block --id` in capitals would have gone past the check. An answer
  to `checkEditing` that is neither a uuid nor `false` refuses the write
  with `reason: "editor_state_unknown"`. A block entered in the milliseconds
  between the question and the write is not covered.
- A block the CLI inserts no longer opens in Logseq's editor. Logseq takes an
  unset `focus` as true and edits the new block (`api.cljs` `insert_block`,
  `editor.cljs` `api-insert-new-block!`, 0.10.15); on the page you were
  looking at, your cursor jumped
  into the agent's block and whatever you typed next landed there (measured,
  0.10.15). Every `insertBlock` and `appendBlockInPage` now goes with
  `focus: false`, and the cursor stays where it was.
- A write of several blocks at once no longer leaves the last of them open
  in Logseq's editor, and no longer pulls your cursor out of the block you
  are typing in. `insertBatchBlock` opens its last block once the page is on
  screen, with no option against it (`editor.cljs`
  `edit-last-block-after-inserted!`; measured, 0.10.15:
  16–34 ms after it answered). With nobody typing, the agent locked itself
  out: its next write to its own block ended in `open_in_editor`. The CLI
  now watches for that block after each batch, ten pauses of 10 ms with a
  question before, between and after them, and closes the editor once it
  shows. On a page that is not on screen nothing opens and the window runs
  full: about 115 ms for each multi-block write (a question takes 1.2 ms,
  measured). While a block
  is open, a tree goes block by block with `focus: false` instead, and a
  write with `--keep-ids`, which only the batch can do (#31), is refused
  with `reason: "open_in_editor"` before anything is written. Only a block
  of the batch is closed: one someone else enters within that window stays
  open, since Logseq does not save a block left while its last editor
  operation is the batch (`lifecycle.cljs` `will-unmount`, `editor.cljs`
  `paste-blocks`; read in the code, not measured). Should `checkEditing` give no usable answer
  after the batch, the write fails with `reason: "editor_state_unknown"`,
  and the blocks that landed count in `writes_landed`.
- A page or journal the CLI creates no longer turns Logseq's view to it.
  `createPage` without options redirects the view to the new page (measured,
  0.10.15); the CLI now sends `redirect: false`.
- A journal the CLI created began with a line `journal?:: true`. The CLI sent
  `journal?` as a page property, and Logseq writes a page property into the
  file; the property was never needed, since Logseq tells a journal by its
  name in the graph's date format (measured, 0.10.15: `journal?` true,
  `journalDay` set, the file under `journals/`, with no property). No write
  sends it now. Journals created that way before keep their line.
- A page the CLI creates before writing to it no longer starts with an empty
  block, the place the property line had taken. Those writes now create the
  page with `createFirstBlock: false`, and the file holds only what was
  written.
  `create-page` without `--content` keeps the empty block: without it Logseq
  writes no file for the page (measured), and a re-index would lose it.
- A write Logseq threw on no longer counts as done. Logseq answers such a
  write with HTTP 200 and `{"error": …}` instead of an error status
  (measured, 0.10.15: a malformed block id on `updateBlock`, `removeBlock`,
  `upsertBlockProperty` and `removeBlockProperty`, `renamePage` from a page
  that does not exist, a method Logseq does not know), and the CLI handed
  the object on as the write's answer. Every write now ends there with exit
  1 and `reason: "logseq_error"`, naming the `method` and Logseq's own text
  as `logseq_message`. An answer that carries a `uuid` is a block, as
  `get-block` already told them apart, and passes. Reads are unchanged.
  `replace-text` goes on with the other blocks, as before, and lists the
  refused one as `failed`, now with `logseq_error` as its reason in
  `failed_reasons` (see below) instead of `write_not_verified`.
- `rename-page` onto the name of another page no longer merges the two.
  Logseq answers `null` and moves the renamed page's blocks to the other
  page, the renamed page gone; to an empty name it answers `null` too and
  does nothing (measured, 0.10.15). Both times `rename-page` reported
  "Renamed". It now asks for the new name first and refuses with
  `reason: "rename_refused"`, `why: "exists"` or `why: "empty"`, before
  anything is sent; a name of spaces only counts as empty. A change of case
  is the same page and still renames. `--dry-run` runs the same check and
  refuses alike, at the cost of one more read. The new name is sent
  trimmed as Logseq trims it, the form the check looked up, and the run and
  its preview report it in that form, as the page is then called. Logseq
  trims with JavaScript's `trim` (`handler/page.cljs` `rename!`, 0.10.15,
  read in the source), which also takes off a byte order mark (U+FEFF);
  trimmed with Python's rules, a name behind one passed the check, the two
  pages merged, and the run said nothing was written.
- A `((uuid))` written by any command now gives the block it points at an
  `id::` line, as Logseq's editor does when a ref is copied (#95). Left to
  Logseq, the target got the line in column 0 and not in its database
  (`get-block --json` showed `properties: {}`), and a later
  `set-block-property` on the target dropped it from the file; parsed again
  without it, the block would get a new uuid and the ref would point at
  nothing (not measured: that needs a re-index). The CLI now stores the id the
  way the editor does before it writes the ref; measured on 0.10.15 for
  `update-block`, `insert-block` (`--content`, `--tree`), `add-note-content`,
  `add-block-ref` and a ref as a `set-block-property` value. A ref in a code
  block or in inline code is no ref to Logseq and changes nothing, a target
  that has its id is not written again, and a dead ref stays as written.
  Measured along the way, and so no longer feared: when Logseq reads such a
  file again, it takes a column-0 `id::` line as the block's id.
- `--resolve-refs`, the unresolved-ref count and `set-todo-status
  --follow-refs` read a ref in capitals, `((8F2A…))`, as the ref it is to
  Logseq (measured, 0.10.15); they took lower case only and left it as a
  hole. One pattern now defines a Block Ref for every reader and writer.
- `update-block`, `set-todo-status`, `set-block-property`,
  `remove-property --id`, `add-journal-block --upsert-heading` and the
  `--property` option of `insert-block` and `add-note-content` reported
  success for a write Logseq had not done.
  `updateBlock`, `upsertBlockProperty` and `removeBlockProperty` answer
  `null` whether they wrote or not, for a uuid no block has too (measured,
  0.10.15). The three API methods now read the block back right after the
  write and fail with `reason: "write_not_verified"`, naming `method`,
  `expected` and `got`. An update is compared by its text, without the
  whitespace Logseq trims at either end, a ref to the block itself, which
  Logseq drops from its text (both measured, 0.10.15), `id::` lines and
  `:LOGBOOK:` drawers, and
  the properties it carries along by their stored values; a property by
  its stored value, without surrounding spaces, which Logseq's parser
  trims; a removed property by its absence, under the key as Logseq's
  parser stores it and as sent (a key the parser would drop is removed as
  given, and the database may hold it so), and under any spelling of it:
  a key an earlier version or another client stored as given, `Status`
  say, stays when `status` is removed (Logseq removes the keyword it is
  sent; `editor/property.cljs` `remove-block-property!`, 0.10.15, read in
  the code), and `remove-property --key Status` reported it removed. A block that does not exist
  fails the check: it holds no key, which would otherwise read as removed.
  `set-property`, which checks that the page shows the value, now fails
  with `reason: "write_not_verified"` too.
- `replace-text` counted a replacement that changed a task marker as not
  written when Logseq's time tracking is on, its default: Logseq then
  appends a `:LOGBOOK:` drawer or rewrites its last `CLOCK:` line
  (upstream `editor.cljs` `with-marker-time`, `util/clock.cljs` `clock-in`
  and `clock-out`, 0.10.15; read in the code, not measured, since time tracking is off in the measured graph),
  and the text read back no longer equalled the text sent. The drawer is
  now left out of the comparison on both sides.
- `remove-block`, `delete-block`, `copy-block --remove`, `delete-page`,
  `rename-page` and `create-page` reported success for a write Logseq had
  not done, as did every command that creates a page or journal before
  writing to it, and the removal of a page's emptied property block. `removeBlock`, `deletePage` and `renamePage` answer `null`
  whether they wrote or not (measured, 0.10.15). The API methods now read
  back: a removed block and a deleted page must be gone, a renamed page must
  answer under its new name with its own uuid and the new spelling (a change
  of case included), and a created page must be the one its name finds.
  Otherwise they fail with `reason: "write_not_verified"`. A deleted page
  that other pages name as their namespace (`Project` beside
  `Project/Alpha`) stays without blocks, as Logseq keeps it (measured,
  0.10.15), and counts as deleted. A page a block of another page links to
  is removed whole (`page.cljs` `delete!`; measured, 0.10.15).
- Storing the id of a ref's target (#95, above) is read back too: each
  target must then hold `id` among its properties, or the write fails with
  `reason: "write_not_verified"`. Each stored id counts in `writes_landed`.
  A ref to a page's property block no longer asks for an id: Logseq skips
  that block (measured, 0.10.15).
- `createPage` on a page that exists answers that page and drops the
  properties sent (measured, 0.10.15). The API method now refuses such a
  page with `reason: "page_exists"` before anything is sent. The commands
  check first, as before, and no command sends page properties now; the
  refusal holds for any caller that would.
- `create-page` on a page that exists now fails with
  `reason: "page_exists"` under `--json`; the error had no reason.
- A page or journal the CLI created under a name Logseq changes no longer
  reads as not created, and no longer lands beside the page meant. Logseq
  creates a page under a cleaned title: trimmed (by JavaScript's rules,
  which take a byte order mark too), `[[…]]` unwrapped, a
  leading `#` and a slash at either end dropped (`handler/page.cljs`
  `create!`, 0.10.15; measured for each). The cleaning is one pass and not
  idempotent: `#[[X]]` becomes `[[X]]`, which cleaned again would be `X`
  (measured, 0.10.15). A journal title it takes as one in any graph
  (`Jan 1st, 2099`, `2099-01-01`, `2099_01_01`; `date_time_util.cljs`
  `safe-journal-title-formatters`) becomes the journal under the graph's own name, and `createPage` answers
  it with `null` (measured, 0.10.15, for `Jan 1st, 2099` in a
  `yyyy-MM-dd, EEEE` graph; the other forms not measured). Asked for under
  the name as sent, neither page is found: `create-page --name "[[X]]"`
  failed with `write_not_verified` after creating X, and did not see an X
  that existed; `add-note-content --page "Jan 1st, 2099"` and
  `insert-block --page` with such a name created the journal and then
  failed to write to it. `create-page`, `add-note-content`,
  `insert-block --page`, `add-block-ref --page`, `copy-block --to-page`
  and every `--keep-ids` write to the end of a page now use the name
  Logseq creates, worked out once from the name given, for the check for a
  page that exists, `--dry-run`, the write and the output, and write into
  a journal of that name that exists. An alias is followed from that name,
  not from the one given: `--page "[[X]]"`, `#X` or ` X ` for an alias X
  writes to X's page, as `--page X` does. `createPage` itself is sent the name
  as given, which Logseq cleans into that name; a journal title goes in
  the graph's format. Under `--json`, `add-note-content` names that page in
  `position`, in the run as in its preview; `page` stays the name asked
  for, as in every result. Its text output names the page the same way in
  the run as in the preview, in quotes, where the run left them out. The weekday goes in lower case
  (`2099-01-01, thursday`); Logseq keeps its own spelling of the title
  (measured). A date in a format Logseq takes for a journal only in a
  graph of that format, such as `01.01.2099`, stays a page of that name, as
  Logseq creates it.
- `move-block` with a block id in capitals reported that the move did not
  take effect after Logseq had made it, and let a target inside the block's
  own subtree past the refusal that names it. Logseq's uuids are lower
  case, and both checks compared the ids as typed. They are now taken in
  lower case, the move sent that way too.
- A tree whose parent block Logseq did not find ended with "Nothing was
  written." even when writes of the same call had landed before the
  check: the page or journal created first, the heading, or the parent
  block of a tree whose children then go as one batch. The message now
  names the writes that landed, from the same count as every refusal.
- Under `--json` a command that printed a note on stderr and then failed
  left stderr no JSON: the note stood in front of the error object. Every
  write can be refused after its notes, so this was reachable from each
  note a writing command prints first, such as the deprecation note of
  `add-journal-entry`, a dropped `id::` line or "Hierarchical content
  detected"; `get-journal-range` printed "showing N of M" before a
  `partial_read` error. Under `--json` notes are now held until the
  command ends: a failure carries them in its error object as `notes`,
  and otherwise they are printed after the result. `get-backlinks`, which
  dropped its fallback warning under `--json` for that reason, reports it
  the same way now. Without `--json` nothing changes.
- Content that is nothing but the page's title heading (`# <page name>`)
  is refused before anything is written. The heading is dropped since the
  page shows its name, and the check for empty content ran before that:
  `add-journal-block` wrote an empty block with exit 0, and
  `add-note-content` and `add-journal-content` reported "Added 0 block(s)"
  with exit 0, `--property` dropped with a warning. The check now sits
  where the heading is removed, for every writer that removes it, including
  `add-journal-entry`, in the run and under `--dry-run`: exit 1, under
  `--json` an error object with `reason: "empty_content"` and the `page`.
- `insert-block --page`, `add-block-ref --page` and `copy-block --to-page`
  on a page that does not exist no longer leave an empty block at its top.
  `appendBlockInPage` created the page itself, with an empty block before
  the one written (measured). They now create the page first with
  `createFirstBlock: false`, as `add-note-content` does, under the name
  Logseq creates it with, so a journal title in another format writes to
  the journal. `add-block-ref --page --dry-run` now reports
  `would_create_page: true` for such a page. A `--keep-ids` write creates
  the page as before, after the check for a block open in the editor.
- `insert-block --dry-run` with `--after`, `--before` or `--child-of` and a
  uuid no block has no longer previews an insert the run then refuses. The
  preview ended before reading anything, with exit 0. It now reads the
  anchor, as `move-block --dry-run` does, and fails with exit 1, under
  `--json` with `reason: "block_not_found"` and the `id`. One read more per
  preview.
- Indented lines below a `# <page name>` title in the content now keep
  their levels. The writers drop that title, since the page shows its
  name, and then stripped the text, which took the indentation off the
  first line only: `- first` and `- second`, indented alike, were written
  as parent and child. The lines now lose the indentation they share. A
  `# <page name>` line anywhere else, further down or in a code block, was
  dropped too; only the first line with text is taken for the title now.

### Changed

- A write the CLI refuses, or cannot show Logseq did, now fails like every
  other error: under `--json` as one error object on stderr with a `reason`
  (`open_in_editor`, `editor_state_unknown`, `logseq_error`, `page_exists`,
  `rename_refused`, `write_not_verified`), the fields that go with it and
  `writes_landed`, the writes of the same call that landed before it. The
  message ends with the same count in words, or "Nothing was written.":
  there is no rollback, and a retry would write those again. These failures
  were `click.ClickException`s, which under `--json` print neither JSON nor
  a reason (measured: stdout empty, stderr `Error: …`), so an agent could
  not tell a block open in the editor from a write Logseq ignored. The CLI
  counts the landed writes itself, one per write and a batch by its blocks,
  so no command has to keep its own count for the message. A failure of the
  connection after writes of the call landed (`connection_refused`,
  `timeout`, `bad_response`, `http_error`) names them the same way, and a
  write sent and not yet proven when it came as `unproven_write`: it may
  have landed. A write in doubt stays named when the call writes on after
  it, as `--keep-ids` does to remove the empty block it wrote to anchor a
  batch on an empty page. Before the first write these errors read as they did. Exit
  status stays 1. Checks of the input before the first write are unchanged: an
  anchor that does not exist for `--tree` or `--keep-ids` still fails with
  an `Error:` line, and `update-block` on an id no block has with an error
  object without `reason`.
- An insert Logseq answers without a block now fails with
  `reason: "write_not_verified"`. `insertBlock` and `appendBlockInPage`
  answer `null` for a write they did not do (an unknown anchor, a page not
  loaded); each command checked for the uuid itself and failed with
  "Logseq did not create …", which under `--json` carried no reason. The
  check sits in the two API methods now, so no command can skip it, and the
  error names `method`, `target`, `expected` and `got`. An insert counts in
  `writes_landed` once proven, and a page created earlier in the same call
  counts too: `create-page --content` whose text did not land says one
  earlier write remains, not "Nothing was written."
- `--under-heading` no longer falls back to the top of the page when the
  heading could not be created. `add-journal-block`, `add-journal-content`
  and `add-block-ref` then wrote the blocks at the top with a warning and
  `position: "top-level (heading not found)"`; `add-note-content` exited 1.
  A heading Logseq does not create now fails like any insert, before a
  block of the text is written.
- A write of several blocks at once, and every `--keep-ids` write, now fails
  with `reason: "write_not_verified"` when Logseq did not write all of it,
  and names what landed and what is missing: `method: "insertBatchBlock"`,
  `expected: "5 blocks"`, `got: "3"`, and the three that landed in
  `writes_landed`. `insertBatchBlock` answers `null` whatever it did, so the
  CLI reads the place it wrote to before and after; that check sat with the
  callers and failed with "Batch insert wrote 3 of 5 block(s)", which under
  `--json` carried no reason. It sits in the API method now, and also
  compares each new block's text with what was sent. A `--keep-ids` write
  whose blocks came out under other ids or in another place fails the same
  way. On a page with no blocks such a write goes after a stand-in block
  that is removed again; a stand-in that cannot be removed is now named in
  the error of a batch that failed, instead of that error being lost, and
  counts in `writes_landed`, while one that was removed does not.
- `move-block` that Logseq did not carry out fails with
  `reason: "write_not_verified"` and `method: "moveBlock"`, where it failed
  with "Move of … did not take effect" and no reason under `--json`. The
  check reads the block back where it was sent, as before, now in the API
  method.
- `replace-text` names the reason of each block it did not write in
  `failed_reasons` (`{id: reason}`), in its report and in the error object,
  beside `failed`, which stays the list of ids, and `writes_landed`. The
  error's `reason` is the blocks' common one, or `write_not_verified` when
  they differ. A block open in Logseq's editor fails alone, and the other
  replacements are written. The text report names the reason after "not
  written".

## [0.15.0] - 2026-09-25

### Added

- An Agent Skill, `skills/logseq-cli/SKILL.md`, for an agent that has not met
  the tool yet: why the database and not the Markdown files, `doctor` first,
  and four habits (a journal by its date, `--dry-run` before destructive
  writes, bounded reads, the exit status). For the commands and their
  options it points to `--help`, which describes the version installed;
  AGENTS.md on `main` stays the reference for the workflows (#88). It
  lives in the repository only, under `skills/<name>/`, where the Agent
  Skills specification wants the folder to match the name and
  `npx skills add` looks; a `skill install` command would cover two agents
  where that installer covers most. The frontmatter holds only fields of the
  specification, since claude.ai refuses one agent's own.
  `tests/test_skill.py` checks every command and option the skill names
  against the CLI. Measured while writing it, and so left out: a page file
  Logseq reads again keeps its blocks' uuids, so "the uuids break" is no
  reason against editing the files.
- `ruff check` runs in CI, with the rule set named in `pyproject.toml`
  (`E4`, `E7`, `E9`, `F`) rather than taken from ruff's default, which later
  versions widened. ruff is pinned in the `dev` extra, so a local run checks
  what CI checks. The 57 findings were fixed, not ignored: unused imports,
  a variable and f-strings without placeholders, `l` as a name, and lambdas
  assigned to a name.

- `--content-file FILE` on `update-block`, `insert-block`, `add-note-content`
  and `add-journal-content`, where only `add-journal-block` had it. A call
  reaching for it on `update-block` failed with "No such option", and the way
  left, `--content "$(cat FILE)"`, puts the shell back between the text and
  the CLI. On these commands it is `--content` read from a file (`-` reads
  stdin) and nothing more: the text goes through the same path, the one-block
  rule from #47 included, and a test holds that the same text sent both ways
  makes the same calls. `add-journal-block` keeps its own meaning, the file as
  one tree. The either/or is decided in one place for the four; `add-journal-block`
  keeps its own check, since its `--content` is repeatable, and answers with
  the same words.
  See [#49](https://github.com/muellerei/logseq-cli/issues/49).
- A write of one block's text says so when a quote in it stops at a blank
  line: `> first`, an empty line, `second` shows `second` as plain text,
  since Logseq's parser ends a quote there. It happened in real use, and the
  write had reported nothing. The text is written as sent and stays one
  block, so this is a `Note:` on stderr, naming the block and the line and
  the fix (start the blank line with `>`), not a refusal. It comes after
  every check that can refuse, so a refused write under `--json` still
  leaves one JSON object on stderr. The rule is narrow and measured with
  mldoc 1.5.7, the parser version Logseq 0.10.15 pins: no note for a quote
  kept by a `>` line, a lazy continuation, a second quote, a property line,
  code or a closed org `#+BEGIN_` block, or a line holding only a no-break
  space, which mldoc reads as text. A `>` alone after the blank line opens no
  quote and is noted. A fuller model of mldoc's quote grammar was built and dropped
  in #45; this needs none of it. Outline text puts every line in a block of
  its own, so `add-note-content` and `add-journal-content` have nothing to
  note, and `replace-text` changes text already in the graph, where a note
  could not tell a quote it made from one that was there.
  See [#50](https://github.com/muellerei/logseq-cli/issues/50).

### Changed

- The documentation promised three different things about exit codes, and
  the code kept none of them. The README said there was deliberately no
  second exit code; AGENTS.md and the agent skill said 1 meant a failure and
  2 a refused call, and told agents to rely on that; in the code most refused
  calls exited 1 and a few exited 2. All of them now say what the code keeps:
  0 means the call did what it says, non-zero means it did not, the error
  says why, and the number itself carries no meaning. No exit code changed.
  AGENTS.md also promised that a `--dry-run` exiting 0 means the real call
  would succeed; `create-page --dry-run` on an existing page reports
  `would_create: false` with exit 0, and a preview needs no `--force`, so it
  now says that instead. A test checks that no document or help text gives
  1 or 2 a meaning again and that no new error sets its code by hand. Three
  exit codes derived from the kind of error were worked out and deferred,
  because nothing in use showed a caller needing them; ADR 0004 records why
  and when to revisit.
  See [#93](https://github.com/muellerei/logseq-cli/issues/93).
- `helpers.py` is gone. 97 of its 101 functions and constants now live in
  eight new modules named for what they decide: `dates`, `headings`,
  `outlinetext`, `ids`, `cliinput`, `blockprops`, `strictinsert` and
  `lookup`. The other four went to modules that already asked the same
  question: `process_blocks`, `extract_page_links` and `extract_topics` to
  `render.py`, and `uuid_fields` to `output.py`. ADR 0003 says why the split
  follows what the code decides rather than whether it needs the API, and
  which other layouts were measured.

  Nothing about using the tool changes. Every function and constant moved as
  it was. A script compared each one's syntax tree with the one it had in
  `helpers.py`, and every other file with its previous version. It allowed
  only imports, the docstrings and comments that named `helpers`, the
  description at the top of `render.py`, and the two test changes below
  (#87). The `--help` output of all 38 command names was captured before the
  first commit and diffed after every one of them. `logseq_cli.helpers` no
  longer exists as an import path, and `git blame -C` follows the moved lines
  to their origin.

  The test that finds every command that writes used to read `helpers.py` and
  the command modules by name. It now reads every module in the package, so a
  function that writes is still found after it moves.

  Two tests in `tests/test_package_layering.py` keep the new layout from
  drifting: the package's import graph has no cycle, imports inside functions
  included, and `dates`, `outlinetext` and `cliinput` take no `api` and
  import no module that does. Each was shown to fail on the change it guards
  against.

### Fixed

- A page's backlinks that could not be read came back as none, with exit 0.
  `get-backlinks` printed "No backlinks found" when Logseq's backlink request
  and the fallback scan both failed, `get-page-stats` reported
  `inbound_count: 0`, and the scan skipped pages it could not read.
  `get-backlinks` now names the page, answers the other pages and fails
  after them; `get-page` prints the page, marks its backlinks
  (`backlinks_error`) and fails after the output; `get-page-stats` fails.
  All three name the pages in `backlinks_unread`. A timeout ends the call at
  once instead of being waited for again on every further page.
  See [#93](https://github.com/muellerei/logseq-cli/issues/93).
- Two errors that now reach the caller more often ended as a traceback: a
  read timeout and an answer that is not JSON. They end with reasons
  `timeout` and `bad_response`. `get-page --resolve-refs` called a ref dead
  when looking up its block failed; only a lookup that answers null means
  that now, and a failed one fails the call.
  See [#93](https://github.com/muellerei/logseq-cli/issues/93).
- A failed read was taken for absence. The journal writers and
  `add-note-content` asked whether the page exists and read any error as
  "no", then created the page, which may well have been there; `get-properties`
  read an error on the page's first block as "no properties", exit 0. Logseq
  answers null for a page that does not exist, so an error there is a failed
  read, and it now fails the call.
  See [#93](https://github.com/muellerei/logseq-cli/issues/93).
- `analyze-graph`, `find-knowledge-gaps`, `analyze-journal-patterns` and
  `suggest-connections` counted a page they could not read as empty, so a
  connection that dropped halfway through a scan gave wrong numbers with
  exit 0. Reading every page of a real graph raised nothing, so such an
  error means the connection: it now reaches the caller instead of a wrong
  number.
  See [#93](https://github.com/muellerei/logseq-cli/issues/93).
- `add-journal-block` with several `--content` values under a heading it
  could not find or create wrote the blocks at the top of the page, as the
  single-value path does, but reported `position: "under '<heading>'"` and
  gave no warning. It now warns and reports `top-level (heading not
  found)`, like the single-value path.
  See [#93](https://github.com/muellerei/logseq-cli/issues/93).
- `get-page --heading` with a heading the page does not have printed a
  warning and then "(empty page)" with exit 0: asked for one section and
  got none, which is not an empty page. It now fails, names the page (under
  `--json` in `heading_not_found`), and still prints the other pages of a
  batch first; in text mode the page reads "(no heading '...')".
  See [#93](https://github.com/muellerei/logseq-cli/issues/93).
- Example scripts hid failures. `backup-graph.sh` ended each page in
  `|| true` with stderr discarded, so a page that failed left an empty file
  and the backup was reported done; it now names the page, leaves no file
  and exits non-zero. `export-all-pages.sh` counted such empty files as
  exported and now does the same. Both wrote two pages whose names sanitize
  alike (`a/b`, `a_b`) to one file, the second overwriting the first; the
  second is now reported as not exported. `daily-todos.sh` never listed a
  task, because it read the result rows as blocks, and said "No results or
  Logseq not running" for any failure; it lists them now, and with
  `pipefail` a failed query says so and exits non-zero.
  `top-pages-pipeline.sh` was headed "Top 10 Pages by Reference Count" but
  lists the first 20 pages by name; the heading now says so.
  See [#93](https://github.com/muellerei/logseq-cli/issues/93).
- `delete-page`, asked interactively and answered with "n", printed
  `Aborted.` and exited 0, though nothing was deleted. It now fails with
  `reason: "declined"`. Scripts are not affected: without a terminal the
  command never asks and requires `--force`.
  See [#93](https://github.com/muellerei/logseq-cli/issues/93).
- `get-journal-range` caught the error of each day into an `error` field
  and exited 0, even when the connection dropped halfway through the range.
  It still prints every day, and then fails with `reason: "partial_read"`
  and the days it could not read, also when `--max-chars` cut those days
  from the output.
  See [#93](https://github.com/muellerei/logseq-cli/issues/93).
- `set-block-property` reported a property on a block id that does not
  exist as updated, with exit 0, and wrote nothing. Logseq answers the write
  with `null` whether it landed or not (measured against a live graph, for
  both cases), so the write path could not tell; only `--dry-run` read the
  block first. Both paths read it now and fail with `reason:
  "block_not_found"`. One more read per call.
  See [#93](https://github.com/muellerei/logseq-cli/issues/93).
- `replace-text --json` exited 0 when a replacement did not reach the graph:
  the blocks are read back to check, and a miss only showed as a `failed`
  field in the output. Without `--json` the same case already exited
  non-zero. Both modes now print their report and then fail, under `--json`
  with an error object carrying `reason: "write_not_verified"` and the
  `failed` ids.
  See [#93](https://github.com/muellerei/logseq-cli/issues/93).
- `get-todos --help` said `--due-from/--due-to` exclude repeating tasks
  because Logseq stores only their first occurrence, and the comment above
  the due filter said the same. Both described an earlier design: a
  repeating task is placed by its next occurrence, derived from the date in
  its text, and only one whose interval cannot be read is left out. That date
  is also not always the first one — Logseq moves it on when the task is
  ticked off by its checkbox.
  See [#90](https://github.com/muellerei/logseq-cli/issues/90).
- `find-block --with-children` and `get-backlinks --with-context` printed a
  block's second and later lines at column 0, the gap #75 closed for
  `get-page`: a property line of the block read like part of the listing. They
  now sit under the block's first line, and under `--with-context`, where the
  linking blocks follow one another, two spaces deeper, so where one block
  ends stays visible.
  See [#77](https://github.com/muellerei/logseq-cli/issues/77).
- `get-properties` reported a first block's own `id`, `heading` or `collapsed`
  as the page's properties on a page without any: its fallback to the first
  block, kept for pages the old `set-property` wrote there, took every key the
  block had. Measured on a real graph, that was 754 of 918 pages, among them
  every page starting with a heading. The fallback now leaves out the keys
  Logseq keeps for a block itself (`hidden-built-in-properties` in its graph
  parser, with the flashcard keys), so such a page has no properties, as in
  Logseq. A key of the user's own in a first text block is still shown as the
  page's: the fallback cannot tell it from what the old `set-property` put
  there, and it is kept for those pages.
  See [#82](https://github.com/muellerei/logseq-cli/issues/82).
- `set-property` did not make a page property Logseq could find. It wrote with
  `upsertBlockProperty` into the page's first block, and Logseq takes a page's
  properties from its property block only when that block is saved, which that
  call skips (measured against 0.10.15, and in `save-block-inner!`). On a page
  with a property block, a new key or value reached the file but not the page
  until Logseq read the file again: `query-pages-by-property` missed the page,
  `get-properties --property` exited 1, and an alias just set was no alias
  yet. On a page whose first block held text, the property went into that
  block, where it never counted as the page's. Both commands now rewrite the
  property block's lines and save it with `updateBlock`; a page without one
  gets one before its first block, inserted empty and then filled, since a
  block inserted with the text is not taken for one. A first block of property
  lines only, which the old way left on a page created empty, becomes the
  property block, and it goes with its last property, unless it is the page's
  only block. The page is read back after the write, and one it does not show
  exits 1. `title` is refused: saved there, it renames the page, past every
  check `rename-page` makes (measured). So is `collapsed`, which Logseq reads
  as the block's folded state and never shows as the page's. The block's text
  is read again just before it is written back whole, so a key a parallel call
  set in between is kept. Setting a value the page already shows writes
  nothing and says `unchanged`; one whose line is there but which the page
  does not show, as the old way left it, is saved again, first empty and then
  with its text, since Logseq saves only a change. Removing a key that is not
  set says so instead of "Removed". `--dry-run --json` names the `target`: the
  property block, or a new one. A key older versions stored verbatim, which no
  file line can hold, is still removed by name.
  See [#80](https://github.com/muellerei/logseq-cli/issues/80).
- `get-page` and `get-journal-range` printed the second and later lines of a
  block at column 0, in both text formats, and so did `get-block` and
  `find-block --with-children` for the children they list. A reader could not
  tell which block a line belonged to, and a property line of a nested block
  read like one of the page's. They now sit under the bullet, two spaces in,
  blank lines too: the layout Logseq writes to the page file (measured against
  0.10.15), and the one write previews already used. Logseq itself was not
  misled: fed back as a page file, the old output parsed into the same tree.
  `get-journal-summary`'s `content` and the page lengths `find-knowledge-gaps`
  measures follow, since they are the same text, so a page near its 100- or
  200-character mark can change sides; on a real graph the same pages were
  listed. The task counts of `analyze-graph` and `analyze-journal-patterns`
  did not change there. A task marker opening a further line, which Logseq
  does not read as a task, no longer counts as one, unless the line above is
  only dashes: the pattern still reaches across that line break, as it did
  before.
  See [#75](https://github.com/muellerei/logseq-cli/issues/75).
- README and AGENTS.md promised every error as a JSON object on stderr under
  `--json`. Most are, but a refused option (exit 2) and a write Logseq
  dropped still come as a plain `Error:` line; both now say so, and name the
  exit status as the signal to rely on.
- `add-block-ref` wrote a ref to a block that does not exist. Only
  `--dry-run` looked the source up, and it warned and exited 0; the real call
  wrote `((uuid))` for a mistyped uuid, exit 0, and the ref rendered as
  nothing. Measured against 0.10.15, a plain lookup is not enough either:
  once the page holding a dead ref is read from its file again, `getBlock`
  answers that uuid with a placeholder, a block without a page. The source is
  now looked up before the page or the heading is written, `--dry-run`
  included, and anything but a block on a page is refused. The ref carries
  the uuid Logseq hands back: `" <uuid>"` from a copy wrote `(( <uuid>))`,
  which Logseq does not read as a reference, and a uuid in capitals is found
  as well. Input that was no uuid at all could create the heading before a
  later check refused it with "Nothing was written"; it is refused first now,
  exit 1 as a missing block rather than exit 2.
  See [#70](https://github.com/muellerei/logseq-cli/issues/70).
- `add-journal-block --upsert-heading` dropped the properties of the block it
  replaced: it called `updateBlock` with the new text alone, and measured
  against 0.10.15, a `prio:: 1` line was gone afterwards (the `id::` line
  stays, Logseq writes it back itself). The replacement now carries them the
  way `update-block` does, a key the new text sets included (#66).
  On the same path, text that was nothing but `id::` lines was dropped to
  nothing and overwrote the matched block with an empty text, reporting
  "updated"; `add-journal-block`, `add-journal-content`, `add-note-content`
  and `insert-block` wrote an empty block in that case. Each refuses it now
  before anything is written, as `update-block`, `create-page` and
  `add-journal-entry` already did, here under `--json` as an error object
  with `dropped_ids`; so does an
  upsert whose first root was nothing but its id, which would have emptied
  the block's heading. An empty block among others is still written: a
  copied block that held only its id was empty.
  See [#67](https://github.com/muellerei/logseq-cli/issues/67).
- `update-block` kept a property's old value over the one `--content` sets.
  It passes the block's properties back so the update does not drop them
  (#30), and measured against 0.10.15, Logseq lets a passed value win over a
  line of the new text with the same key and drops that line:
  `--content $'x\nprio:: 2'` on a block with `prio:: 1` left `prio:: 1`, exit
  0. A key the new text sets as a property line is now left out of what goes
  back, compared as Logseq stores keys (`due_date` is `due-date`), a line in
  a code block included, since `updateBlock` takes it out of the code block
  (#68). `id` and `custom-id` always go back: the block's uuid is not the
  text's to set. The test stand-in modelled the opposite and follows the measurement now.
  See [#66](https://github.com/muellerei/logseq-cli/issues/66).
- A page alias was read and written as a page of its own. Logseq's HTTP API
  does not resolve an alias, so `get-page --name <alias>` answered an empty
  page with exit 0, `get-properties` `{}`, `get-page-stats` 0 blocks, and
  `find-block --page` found nothing; `add-note-content --page <alias>`
  and `set-property` wrote to a file of the alias's own, which Logseq does not
  show under that name, and reported success. Every command with a page name
  now means the page Logseq would open: a name whose page is empty or a
  placeholder, and that a page names in its own `alias::` property, means
  that page, as `get-redirect-page-name` decides in Logseq's UI. Measured on
  Logseq 0.10.15 with a file graph, 2026-09-23; on the unsupported DB version
  an alias is not followed. Under `--json` a result that names the page keeps
  the name given in `page`, and `alias_of` names the page used. Where it differs from
  Logseq, it says so: an alias two pages claim is refused with both named,
  where Logseq takes the first, and `delete-page` and `rename-page` refuse an
  alias and name the page, since they cannot be undone. `:block/alias` alone
  was not enough to find the page: it links a whole alias group, and an alias
  that once got a file matched too. The reasoning is in
  [ADR 0002](docs/adr/0002-a-page-name-means-what-logseq-means.md).
  See [#63](https://github.com/muellerei/logseq-cli/issues/63).
- `replace-text` without `--regex` read backslashes in `--replace` as escapes,
  while `--find` was matched literally: `--replace 'C:\new'` wrote a line
  break, and the read-back check agreed, since the block held what had been
  computed. `x\dy` ended in a traceback and `\g<0>` inserted the match. The
  replacement is now written as given. With `--regex` it stays a template,
  since group references are what `--regex` is for, and an invalid pattern or
  group reference is an error message before any block is written, where it
  was a traceback. `\g<0>` without `--regex` now writes those characters.
  See [#60](https://github.com/muellerei/logseq-cli/issues/60).
- `insert-block --tree-file` named the wrong option when it could not read the
  file: `--content-file not found: tree.md`, an option `insert-block` did not
  have. It reads through the function written for `add-journal-block`, whose
  messages named that command's option. They name the option given now, and
  so do the refusal of `--content` together with `--tree-file`, which named
  `--tree`, and the answer to `insert-block` with nothing to insert, which
  left `--tree-file` out.
- `--content-file -` read stdin in the locale's encoding and kept its line
  endings, while a file was read as UTF-8 with them translated. Under
  `LC_ALL=C`, as in a cron job or an agent's subshell, bytes that are not
  UTF-8 went on to Logseq as lone surrogates; under a Latin-1 locale UTF-8
  text arrived as mojibake; and `a\r\nb` from a pipe kept its `\r` where the
  same file lost it. stdin is now read the way a file is: UTF-8 or refused,
  `\r\n` and `\r` as `\n`. A BOM at the start, which some Windows editors
  write, is dropped from both; it had stayed in front of the first line, so a
  first `- ` was no bullet and a first `# title` was not recognised as the
  page title. Affects `add-journal-block --content-file`, `insert-block
  --tree-file` and, since #49, every `--content-file`.
- `add-journal-block --upsert-heading` without a heading (with `--top-level`,
  or with none configured) created the journal page when it was missing and
  only then refused. The check now comes first, so a refused run writes
  nothing; with several `--content` values, where `--upsert-heading` is not
  used, it is refused the same way instead of being passed over.
- `set-block-property`, `set-property` and `--property` wrote the key `id`,
  and Logseq reads it as the block's id: measured, `id:: plain-text` made
  `plain-text` the block's uuid when the page file was read again, so every
  `((ref))` to the block lost it. The command reported success. `custom-id`,
  which the parser renames to `id`, had been refused since #21; `id` itself was
  not, because only the rename had been measured. The `set-block-property`
  `--help` example wrote exactly that key. Both keys are refused now, and a
  test runs every example in `--help`, epilog and docstring, through the
  parser and every key an example writes through the rule its command
  applies.
  See [#51](https://github.com/muellerei/logseq-cli/issues/51).
- Every write of a block's text applies the `id::` contract now. Logseq reads
  an `id::` line as the block's uuid. `insert-block`, `add-note-content`,
  `add-journal-block` and `add-journal-content` have handled one since #22 and
  #31, and the other writers passed it on. Measured on Logseq 0.10.15, with
  the page file read again after each write:
  - `update-block --content` with a line naming another uuid gave the block
    that uuid. The old one answered `null`, every `((ref))` to the block
    dangled, and the exit code was 0.
  - `create-page --content` made the value the new block's uuid, without a
    word.
  - `replace-text` could turn `ID: <uuid>` into `ID:: <uuid>`, with the same
    effect.
  - `copy-block` wrote the source's line into the copy, so the file named one
    uuid for two blocks until Logseq read it again.

  Now `update-block` keeps the block's own line, the one `get-block` returns,
  so a block read and written back keeps working. A line naming another uuid
  is dropped with a note. `create-page --content` and `add-journal-entry` drop
  the line with a note. `copy-block` drops it without one, since the copy gets
  new uuids anyway and refs stay with the original. `replace-text` refuses a
  replacement that makes such a line, before any block is written
  (`reason: "id_line"`, exit 2, like a line that splits a block). The rule
  sits where #47 put the one-block rule: `LogseqAPI` refuses any `id::` line
  in a write unless the write keeps ids or the block already had that line.
  A writer added later therefore fails loudly instead of replacing a uuid.
  The heading of `--under-heading` is block text as well and is checked
  before the page it goes on is created. Text that was nothing but `id::`
  lines is refused on `update-block`, `create-page` and `add-journal-entry`,
  before anything is written, rather than written as an empty block.
  See [#56](https://github.com/muellerei/logseq-cli/issues/56).
- An `id::` value with a space in it (`id:: a b c`) got past every id check.
  So did one with whitespace around it that Logseq trims off: a tab after
  the space, or a no-break space, form feed, vertical tab or ideographic
  space after the value. The CLI neither dropped nor refused such a line, and
  Logseq takes it as the block's uuid all the same (each case measured on
  0.10.15; with spaces inside, the block afterwards stood twice in the page
  file, one of the two entities without a page). The value is now read the
  way Logseq reads it, trimmed except for a trailing `\r`, which Logseq does
  not take. So the line is dropped like any other, and `--keep-ids` refuses
  a value that is not a valid uuid.
  See [#56](https://github.com/muellerei/logseq-cli/issues/56).

## [0.14.0] - 2026-09-23

### Added

- `--keep-ids` restores an id that only a `((ref))` still holds: the restore
  case, where a block was deleted, other pages still point at it, and the
  outline is written back from a copy. Logseq keeps a placeholder under such a
  uuid; `insertBlock` and `appendBlockInPage` refuse to give it to a new block,
  while `insertBatchBlock` with `keepUUID` and the id as an `id::` line takes
  it over, and the refs resolve again (measured, 0.10.15). Every write with
  `--keep-ids` now goes through that one call, whichever position it writes
  to, rather than a second path for placeholders only; the issue proposed the
  latter, and one path means a restore cannot behave differently from a move
  depending on which ids the graph happens to hold. Without `--keep-ids`
  nothing changes. Two positions needed care, both measured: before a page's
  first block, and on a page with no blocks at all, the batch comes out with
  `* ` in front of every node. The first is written after that block and its
  roots moved before it; the second is written after a stand-in block that is
  removed again. The batch answers `null` whatever it did, so the page is read
  back: the blocks must be there in the number sent, under the ids asked for,
  and where they were sent, or the command fails and says what landed. A
  placeholder is told apart by what it lacks: a block has a page, a page has
  a name, a placeholder neither (measured). An id a page holds is refused like
  one a block holds, and so is a block carrying two `id::` lines: which one a
  batch keeps is not the CLI's to guess, and the other would go unchecked. A
  page that does not exist is created first, as `appendBlockInPage` does
  without `--keep-ids`.
  See [#31](https://github.com/muellerei/logseq-cli/issues/31).
- `get-page --outline` lists a page's headings, one line each with its uuid:
  the table of contents, and the uuid to write under, in one call. A heading
  is what Logseq reads as one (`properties.heading`, set for `## X`,
  `heading:: true` and `heading:: 2` alike), not a pattern over the text, and
  the outline is cut from the same block tree the full read returns. It is
  indented by how the headings nest, not by their number of `#`: a `### B`
  next to a `## A` is not in A's section, and `--heading "## A"` would not
  return it. With `--heading` it outlines that section. It reads no
  backlinks.
  Recorded use read whole pages through `grep "- ##"` for this, then ran
  `find-block` per heading for the uuid.
- `get-page --max-chars N` and `get-journal-range --max-chars N` cut the
  blocks so the output fits. The size is measured in the format printed,
  because a block in `--json` is several times its text; a cap counted on
  content would let JSON overshoot by that factor. The cut falls between
  blocks in reading order, and pages or days past it are not printed. stderr
  names the blocks withheld, the page or day and section the cut fell in, and
  the later pages or days; `--json` carries the same as `withheld` and `cut`.
  `--from-block UUID` continues from the block the note names, with its
  ancestors as context. A first draft suggested `--heading` for that, and
  review broke it three ways: a heading name that repeats on the page, a
  heading rewritten by `--resolve-refs`, and a section larger than the cap,
  where the follow-up read stopped at the same block again. The same draft
  listed pages past the cut with a placeholder each; on short journal days
  the placeholders alone outgrew the cap. A block that does not fit, alone
  or with its ancestors, ends the chain with the `--max-chars` it needs
  (`cut.needs`) rather than naming itself again, and a page named twice is
  refused, since its uuids would be too. Only blocks are cut: page headers
  and backlinks always print, and when they alone exceed the cap the note
  says so. Recorded use had reads over 30 KB, three of them cut with
  `head -N` wherever line N happened to fall.
  See [#26](https://github.com/muellerei/logseq-cli/issues/26).
- `get-todos --match REGEX` filters by what a task says, case-insensitive,
  on the task text without its properties, `SCHEDULED`/`DEADLINE` lines and
  `LOGBOOK`, so a property value cannot match a task that does not say it. It
  is a single option: next to the repeatable `--status`, which ORs, a
  repeatable `--match` that ANDs would read ambiguously, and a regex expresses
  alternatives itself. The JSON shape, `{"todos": [...], "count": N}` with its
  fields, is now stated in `--help` and the README. Each task also carries
  `journal_day`, the day of the journal page its block lives on, so its age
  is one subtraction; it was already read for `--from/--to` and dropped
  before output. Property lines are now recognised by the rule the other
  commands share, indented ones included; `get-todos` had kept its own copy,
  which let an indented `owner:: someone` through as task text. In two days of recorded use, `get-todos --json` ran 7 times, 6 of
  them into an inline script, 5 of those to filter by content, and all 6
  guessed the shape.
  See [#27](https://github.com/muellerei/logseq-cli/issues/27).
- `find-block --uuid-only` prints bare uuids, one per line, for use in
  `$(...)`. With no match it exits 1 and says so on stderr, where the plain
  form prints "No blocks found." and exits 0: an empty `$U` would otherwise
  flow into the next write. It excludes `--json` and `--with-children`. In two
  days of recorded use, `find-block | grep uuid | head -1 | awk` appeared 8
  times; `--first` already existed and saved only the `head -1`.
- `find-block --exactly-one` fails unless exactly one block matches, and lists
  the matches when there are several. The recorded pipelines looked up a block
  to write to, and `--first` (like `head -1`) picks one of several matches
  with the rest named only on stderr, which a `$(...)` does not show. The same
  refusal to guess already guards the `--where-content` selectors of the write
  commands. It excludes `--first` and `--limit`.
- `add-journal-block` and `add-journal-content` print the uuid of the block
  they wrote (the root, for a tree) on a line of its own, as `add-note-content`
  and `insert-block` already did. The same recorded use had 18
  `add-journal-block` calls without `--json`, four of them followed by a
  `find-block` only to recover that uuid. The `Added N block(s)` line is
  unchanged. Where a content preview follows, the uuid line now comes first,
  in `insert-block` too: the preview repeats the content, which may itself
  contain a line reading `uuid: ...`.
  See [#25](https://github.com/muellerei/logseq-cli/issues/25).
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

- `set-todo-status` joined the first two lines of a block whose marker stood
  alone on its line: it looked for the marker by splitting the whole text at
  whitespace, and a line break is whitespace, so `TODO\nnotes` became
  `DONE notes`. A code block under a bare `TODO` lost the line break before
  its fence, which left the closing fence without an opener and, since #47,
  had the write refused. Only the first line changes now.
- Text written as one block could come back from the page file as several, or
  take in the blocks after it. Logseq writes a block's text under one bullet,
  and its file parser reads some lines as block boundaries (measured, 0.10.15):
  after the first line, a `- ` line (indented too, `-` alone) becomes a child
  block and a `# ` line (any number of `#`) a block next to it, with a space,
  tab, form feed or carriage return around the mark (so a CRLF text's `-\r`
  counts); a code fence
  nothing closes, on any line, runs on into the blocks after it up to the next
  code block on the page and swallows them with their uuids. The database keeps
  the block as sent until the file is read again, so the command reported
  success and the damage came later. `insert-block --content` did not check at
  all, although its code and this changelog said it did; `update-block` and
  `add-journal-block` checked only `- ` lines, and refused them inside a code
  block too, where Logseq keeps them. Now every write of block text is checked
  for all three, outside a closed code block: each command checks all it would
  write before the first write, the heading of `--under-heading` included, and `LogseqAPI` checks every write it sends, so
  no command can go around it (`copy-block`, `replace-text`,
  `create-page --content` and the reference of `add-block-ref` included). A
  property value with a line break is refused too: Logseq writes it into the
  block as `key:: value`, where each line after the break is a line of the
  block (`v\n- x` put `x` into a child block). A refusal names the line, what Logseq
  would make of it and the way to write it, exits 2, and under `--json` gives
  `reason: splits_into_blocks` with `line` and `kind`. A change to part of an
  existing block (`set-todo-status`, `replace-text`) may leave as many such
  lines as the block had, since Logseq's own editor makes them, but not add
  one; `set-todo-status` refuses, in the preview too, to put a marker in front
  of an opening fence, which would leave the code block open.
  `add-journal-block` checks the text as it is written, so a value
  `--no-preserve` joins into one line is no longer refused for the lines it
  had. See [#47](https://github.com/muellerei/logseq-cli/issues/47).
- A code block written in outline text (`add-note-content`,
  `add-journal-content`, indented `--content`, `--tree` as text) was cut into a
  block per line: `` ```js ``, `a()`, `` ``` ``. None of them was a code block,
  and the block holding only the opening fence was worse: when Logseq reads the
  page file again, a fence nothing closes in one block runs on into the blocks
  after it, up to the next code block on the page, and swallows them with their
  uuids (measured, 0.10.15). The parser now reads a code block as Logseq's
  files do: from the opening to the closing fence every line is code, `- `
  lines included, with its indentation kept. Without a bullet the fence goes
  on the block above, as a property line does; on a bullet line it is a block
  of its own. See [#47](https://github.com/muellerei/logseq-cli/issues/47).
- An `id::` line indented by a form feed or a carriage return was not seen as
  an id, and Logseq takes it as the block's (measured with `keepUUID`,
  0.10.15). With `--keep-ids`, a uuid another block has could go out behind
  one unchecked, in flat `--content` or a JSON `--tree` node; outline text
  strips each line and was not affected. The property-line rule now takes
  spaces, tabs, form feeds and carriage returns as indentation, and not a
  no-break space or a vertical tab, which Logseq reads as text.
- An `id::` line inside a code block was read as the block's id. To Logseq it
  is code (measured, 0.10.15): `insertBlock` and `appendBlockInPage` write it
  as given, and `insertBatchBlock` with `keepUUID` leaves it in place and gives
  the block a fresh uuid. Without `--keep-ids` the CLI removed the line from
  the example; with it, an example quoting an existing block's uuid was
  refused as a copy, and one quoting a fresh uuid was sent with `keepUUID` and
  failed the read-back after the write. The id rule now takes the code-block
  rule of the property-line mask, as corrected in this release. That is sound
  only because the check, the removal and the write now see the same blocks
  (see "announced as dropped" in this section); a first attempt without that
  was withdrawn in #31. Without `keepUUID`,
  `insertBatchBlock` takes every `id::` line out of the content, one in a code
  block too (measured), so a `--tree` quoting one is written block by block.
  See
  [#43](https://github.com/muellerei/logseq-cli/issues/43).
- An `id::` line that a command announced as dropped could still be written.
  `add-note-content`, `add-journal-block`, `add-journal-content` and
  `insert-block --content` checked the parsed outline for `id::` lines but
  removed them from the raw text, line by line, and the two did not always see
  the same line. A bulleted `\t- id:: <uuid>` one level deeper is a property of
  the block above to the parser; the raw line starts with `- `, so it stayed.
  Logseq then minted a fresh uuid and kept the line (`insertBlock` and
  `appendBlockInPage` write the text as given, measured, 0.10.15), and the file
  named a uuid the block did not have. The check, the removal and the write now
  use the one parsed outline; `add-journal-block` decides once per value
  whether it is written as an outline or as one block. What the commands echo
  (dry-run preview, `--json` `content`, the text preview) is derived from that
  outline too, so the dropped line does not reappear there.
- The code-block rule behind `replace-text` and `get-todos --match` hid too
  much and too little. A ``` line with no closer after it made every later line
  code, and so did a one-line ```` ```x``` ````; to Logseq neither is a code
  block, and the `k:: v` lines after them are properties. A `~~~` fence was not
  known at all, though Logseq hides a property between two of them. Measured
  against Logseq 0.10.15 by writing blocks into page files: a fence line starts,
  after spaces, tabs or form feeds (not a no-break space), with ``` or `~~~`,
  the next fence line closes it
  whichever of the two it uses and whatever follows on the line, and an opener
  nothing closes is no code block. The mask now follows that.
- An `id::` line with a tab after the value was not seen as an id, while
  Logseq keeps it as the block's (measured, 0.10.15). Flat `--content` is
  written as given, so an existing block's uuid could reach a second block
  unchecked. A carriage return does stop Logseq, and the check agrees.
- `insert-block --before` with several top-level blocks (`--tree`, or
  `--content` with indented children) wrote them in reverse: `a, b, c` came
  out as `c, b, a` (measured, 0.10.15). Each root was sent "before" the one
  written just ahead of it. Now only the first goes before the anchor and
  each further one after the previous. The test for `--before` could not see
  it: its mock handed out uuids in order whatever the graph would have done
  with them; the new one reads the page back. `--keep-ids` writes were not
  affected once they went through one batch (#31).
- Whether a line in a block is a property line was decided by two patterns,
  and neither agreed with Logseq. `PROPERTY_LINE_RE` (used by `replace-text`,
  `parse_hierarchical_content` and `get-todos`) accepted no `.` in a key, no
  `k::` with an empty value and, in `replace-text`, no indentation. Logseq
  writes `logseq.order-list-type:: number` itself for every block of a
  numbered list, so `replace-text --find number`
  rewrote that line like text. The renderer behind `--format markdown` had
  its own pattern, which took `std::cout << 1` and `k::v` for properties.

  There is now one rule, and it is the rule Logseq reads by, measured against
  Logseq 0.10.15 by writing 31 kinds of line into page files and reading
  `:block/properties` back. A key ends at whitespace or at one of the
  characters #21 measured for the writer; `/` alone reads, as a namespace.
  It may not start with `#`, and `::` is followed by a space or the end of
  the line; a tab does not count. Between ``` fences a line is code, not a
  property, so `replace-text` now replaces in a fenced example instead of
  answering "No matches", and `get-todos --match` sees it. The writer's
  forbidden set is derived from the same characters, so `set-property` and
  the readers cannot drift apart. `id::` lines, which have a rule of their own
  for every spelling of the key, now need the same space: `id::x` is text to
  Logseq and is no longer dropped as an id.

  Two changes follow from the wider rule. `parse_hierarchical_content`
  merged a bulleted property line into whatever block came last, which the
  narrow rule had kept rare; with umlauts accepted, `- Priorität:: hoch`
  after a nested detail would have landed inside that detail. A bulleted
  property line now merges only when it sits deeper than the block above,
  the shape an agent writes for that block's property (`- ## Plan` /
  `\t- collapsed:: true`); at the same level or above it is a block of its
  own, as Logseq reads `- k:: v`. A continuation line without a bullet
  still always merges. And `--format markdown` drops the bullet only for the
  page's own properties, its first block; other properties-only blocks, an
  empty numbered-list item among them, keep it. `get-backlinks
  --with-context`, which leaves out properties-only blocks as context, now
  keeps a block reading `std::cout << x [[P]]` and leaves out one reading
  `a.b:: [[P]]`.
  See [#39](https://github.com/muellerei/logseq-cli/issues/39).

- `get-todos` left the marker in `content` for `CANCELED` and `WAIT` tasks,
  both markers `set-todo-status` writes itself, and left a bare `TODO` with
  no text as `"TODO"`. The strip was a hand-kept list that had drifted from
  the markers in use. It now removes the word the block's own
  `:block/marker` names, so the list cannot drift again. Found in review of
  `--match`, which made the difference visible: `--match "^ship"` missed
  `CANCELED ship it`, and `--match cancel` hit every cancelled task.

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

- `update-block` rewrote the properties of the block it edited. `due-date::`
  came back as `duedate::`, `created_at::` as both `created-at::` and
  `createdat::`, `zip:: 01234` as `zip:: 1234`, and `tags:: [[Alpha]], beta` as
  `tags:: [[Alpha]], [[beta]]`. Exit code 0, nothing on stderr. It was the most
  frequent write in the usage this was found in.

  The 0.8.0 entry that introduced carrying properties through the write calls
  the round trip lossless. It was measured with `owner:: [[Bob]]`, a key with no
  separator and a value that parses back to itself, and holds only for such
  properties. The cause is the plugin API: `getBlock` hands out property keys
  camel-cased (`dueDate`), and the parsed value rather than the text. Written
  back, Logseq lower-cases the key and prints the parsed value. The camel-cased
  form cannot be turned back into the stored one, because `createdAt` may have
  been `created-at`, `created_at` or `createdat`.

  Properties are now read with a datascript pull, which returns the keys as the
  database stores them and the original text of each value, and `update-block`
  carries that text through. Measured against Logseq 0.10.15 before and after:
  `due-date`, `01234` and `beta` stay as written. The one change left is
  Logseq's own: `created_at` is stored as `created-at` and written that way.
  A markdown heading is no longer turned into a `heading:: 2` line when its
  text is replaced: that value came from the `##`, which the new content
  either repeats or deliberately drops.

  The same map fed four readers, which now use the pull as well.
  `set-property --dry-run`, `set-block-property --dry-run` and
  `remove-property --dry-run` compared a key against camel-cased keys and
  reported "(not set)" for a key that was set. `get-properties` listed keys no
  file contains: its output, plain and `--json`, now names `due-date` where it
  said `dueDate`. So do `update-block`'s `properties` field under `--json` and
  its `keeps:` line under `--dry-run`.
  See [#29](https://github.com/muellerei/logseq-cli/issues/29).

- A mistyped block id passed every "not found" check. For a malformed id
  `getBlock` answers HTTP 200 with `{"error": "... is not a valid UUID
  string."}` rather than `null`, and an error object is truthy, so each command
  took it for the block. `update-block --id foo` reported "Updated block foo",
  `remove-property --id foo --dry-run` reported the key as not set. Found in
  review of the entry above, where reading properties by uuid turned the same
  input into a traceback. `get_block` now returns `None` for an error object,
  and every command says `Block not found: foo` with exit 1 before anything is
  written. Found while fixing [#29](https://github.com/muellerei/logseq-cli/issues/29).

- `add-note-content`, `add-journal-block`, `add-journal-content` and
  `insert-block --content` dropped every `id::` in their content without a
  word. #1 had closed that for `insert-block --tree` only. In real use a page
  was rebuilt with `add-note-content`: a block that a journal entry pointed
  at came back under a fresh uuid, the reference died, and the file still
  carried the old `id::` line under a block the database knew by another id.
  All of them now follow one contract, decided in one place: without
  `--keep-ids` the `id::` lines are removed from the content and the drop is
  announced on stderr; with it, the ids are kept. Removing the line is new:
  left in place it named a uuid the block did not have, and a copy carried the
  original's id into the file, where the next parse finds two blocks claiming
  it. Every spelling Logseq reads as the block's id counts, indented or not,
  in any case, and `custom-id`/`custom_id` too; before, `custom-id::` slipped
  past the check and could hand an existing block's uuid to a batch write.

  Two assumptions behind the old limits were measured wrong (Logseq 0.10.15).
  `appendBlockInPage` does take options — it hands them to `insertBlock` — so
  top-level blocks keep their ids too, and `insert-block --top-level` no longer
  says it cannot. And a well-formed id is not automatically safe to keep. An id
  a block still has is the copy case: `insertBlock` throws on it halfway
  through a write, and `insertBatchBlock` does not check an id given as an
  `id::` line at all. Measured, it wrote the copy over the original in the
  database, whose block list for that page then came back empty while the
  file still held the original. That case is now refused before anything is
  written, including the journal page. An id that survives only as a `((ref))`
  target has a placeholder in the database, and `insertBlock` refuses to give
  it to a new block; it was refused as well until `--keep-ids` learned to
  restore it (see Added,
  [#31](https://github.com/muellerei/logseq-cli/issues/31)).
  An id repeated within the content is refused as well. `add-journal-block`
  rejects `--keep-ids` together with `--upsert-heading`, which rewrites an
  existing block whose uuid cannot change, and with `--no-preserve`, which
  joins the lines and turns `id::` into plain text. `create-page --content` and
  the deprecated `add-journal-entry` are not covered.
  See [#22](https://github.com/muellerei/logseq-cli/issues/22).

- `move-block --before` reported failure for every top-level target, whether
  the move had happened or not, and blamed a cause that did not apply: "A block
  cannot be moved into its own subtree". `moveBlock` answers null either way,
  so the move is checked by reading the sibling order around the target. That
  read asked `getBlock` for the target's parent, and for a top-level block the
  parent is the page, for which `getBlock` answers null (measured, Logseq
  0.10.15). The order is now read from the page tree in that case. Measured
  on the same version, `before` does move a block in front of a top-level
  target, from the same page, from a nested position and from another page.
  The unit tests had missed it because their stand-in answered `getBlock` for
  any parent id; the new one answers the way the API does.

  The subtree case, the one refusal Logseq is known for, is now checked before
  the call, for `--under` as well as `--before`, and reported with its reason.
  A move that still does not show up afterwards is reported as not taking
  effect, without naming a cause nobody checked.
  See [#23](https://github.com/muellerei/logseq-cli/issues/23).

- `move-block --dry-run` previewed moves the real run refuses: a target that
  does not exist, and one inside the source's own subtree. It read the source
  only and answered "Would move" with exit 0. It now runs the same checks as
  the move, so a preview that passes is one the move will not refuse up front.
  Found in review of the entry above.

- `set-property`, `set-block-property` and `--property KEY=VALUE` rewrote any
  value Python can read as a number. The file held `1234` for `01234`, `1.5`
  for `1.50`, `1000` for `1e3`, `10` for `1_0`, and `9007199254740992` for
  `9007199254740993`, which travels as a JavaScript number. Exit code 0,
  nothing on stderr. `nan` and `1e400` became floats JSON cannot carry: the
  command ended in a traceback, and under `add-note-content` and
  `insert-block`, whose properties are set after the content, the content
  stayed without them, so a retry duplicated it.

  `upsertBlockProperty` writes a string verbatim and a number as JavaScript
  prints it. Logseq's own parser makes a number only from ASCII digits up to
  2^53-1, and keeps the text beside it (measured, Logseq 0.10.15). A value is
  now sent as a number only where both hold: the parser would make one, and it
  prints back as typed. Everything else, `01234` included, is sent as typed.
  For a leading zero the database holds the text until Logseq next reads the
  file; the file is right from the start. The conversion had come in with the
  initial import, with no reason recorded. Under `--json` these commands
  report the value as sent, so `-7` or `1.50` now appear as strings.
  See [#35](https://github.com/muellerei/logseq-cli/issues/35).

### Changed

- `remove-block`, `delete-page` and `copy-block --remove` refuse while
  `((block-refs))` from elsewhere point into what they would delete, and list
  where each one comes from. `--ignore-refs` deletes anyway. Before, they
  reported a block count and left every such ref dangling, on a page other
  than the one being changed. In real use a page was deleted with `--force`
  during a rebuild, and the dead ref in a journal entry was only found by
  reading the journal afterwards.

  `--force` does not override the check. A caller that deletes pages
  routinely passes `--force` every time, so a check it switched off would
  not have stopped that case. Refs from inside the deleted set do not count,
  because they go together with their target; for a page that means refs
  from the same page. `:block/refs` holds `((uuid))`, `{{embed ((uuid))}}`,
  `[label](((uuid)))` and a `key:: ((uuid))` value alike (measured, Logseq
  0.10.15), so one query covers all four. `--dry-run` refuses the same way,
  and under `--json` the refusal carries the refs as a list. `copy-block
  --remove` checks before writing the copy and points to `move-block`, which
  keeps the uuids. There, refs from within the source count too: the copy
  carries them under new uuids, and their targets go with the original.
  The page is looked up by the name Logseq resolves, not the one typed:
  `getPage` also normalises the Unicode form and a slash at either end, and
  a name that only matched after that found no refs. The `remove-block` help no longer sends block refs to
  `get-backlinks`, which answers for page names only.
  See [#24](https://github.com/muellerei/logseq-cli/issues/24).

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
  `tags:: red, green, blue` for several values). `get-properties` was
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
