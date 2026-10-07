# Design notes

The decisions that shaped the tool more than any feature did. Most came out of
a defect; the [CHANGELOG](../CHANGELOG.md) carries the full account of what was
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

## Writes are verified, not assumed

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

The same gap exists before a write. A command that changes a block reads it,
builds the new text from what it read and sends the whole text, so a change
made to the block in between would be written over, and the read-back would
pass, because it compares with what was just sent. The API reads the block
again past the cache directly before such a write and refuses with
`block_changed` when the text is another. An `id::` line does not count: Logseq
adds it when a ref to the block is written earlier in the same call. This
shrinks the window to the requests between that read and the write; it does
not close it, as no read can.

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
it. With `[graph] keep_empty_blocks_last` a write at the end of a section goes
before the empty blocks that end it, by `insertBlock` with `before`, and so
touches none of them: the cursor can sit in the empty block the user clicked and
stays there. A first version wrote into the empty block with `updateBlock`, which
the editor gate refuses for an open block (measured, 0.10.15) and which
overwrites whatever another writer, or the user, put there between the CLI's
read and its write; an adversarial pass showed it, and an insert cannot lose
that text. A write of several blocks goes block by block while a block is open,
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
See [0.6.0](../CHANGELOG.md#060---2026-08-07) and [0.8.0](../CHANGELOG.md#080---2026-08-28).

A rename had a silent loss of its own. `renamePage` onto a name that exists
merges the two pages (`merge-pages!` in `page.cljs` `rename!`, 0.10.15), without the
confirmation Logseq's own UI asks for; measured on 0.10.15, it answered
`null` while the renamed page's blocks moved to the other page and the
renamed page was gone. `rename-page` refuses such a name, and an empty one,
before anything is sent.

## Writes can be switched off, checked twice

`[safety] read_only = true` makes every command that writes refuse, and the
place it is checked is not the obvious one. The obvious one is the single
method every write goes through, `LogseqAPI.call`. A check there is too late:
`--dry-run` never calls a write method, so a switch checked only there would
let a preview say "would write" and then refuse the real run; and without
`--dry-run` it fires only after the command has read the graph, resolved
aliases and asked the editor. So each command that writes asks first. It says
so where it is registered (`cls=WriteCommand`, a mark on the command, not a
second decorator whose order could go wrong), and the error handler every
command that writes runs inside asks for the mark after the options are parsed (so `--help`
and usage errors come as before) and before the command's first request. The preview is refused with the rest,
because a preview that cannot be followed by the real run misleads.

The second check is in `_post`, the one place a request leaves the process. It
is there for a write that reaches the network by a path the first check does
not cover, such as a command registered without the mark; a test compares the
commands that carry it with the commands that write, and fails when they
differ. The second check was added as a second line, not after a write got
through.

Both ask one function, which decides whether writes are off and from where
(config file, environment, flag), once per call. The switch only tightens: an
environment value of `false` or a missing flag never loosens a config that
says `true`, since a way to loosen it is a way for whoever is being limited to
do so. An unknown environment value switches writes off, and says so; and
`[safety]` alone is checked strictly, because a misspelt limit does nothing
and nothing would say so. Commands that only read stay tolerant of a config
they cannot use.

What it does not do is stated in [safety.md](safety.md): an agent with the
token or write access to the files is not stopped by a refusal in this CLI.

## A caller can say what it read, and the check is a gate of its own

`block_changed` already catches a block that changed between a command's read
and its write. It cannot catch a block that changed between the caller's read
and the call: the command reads the block fresh and finds it as it is now, so
the write of a decision made on older text goes through. Only the caller knows
what it read, so it says so: `--expect-hash` (`--expect-tree-hash` for
`remove-block`, `--expect-marker` for a task's marker), and the command refuses
with `precondition_failed` unless the block is still that. The two are not one
check: one compares the command with itself, the other compares it with the
caller.

The hash is of the block's content, with what Logseq rewrites on its own
taken out (collapsing, `id::` lines, the clock lines of the logbook), because
a hash that moved when a block was folded would refuse writes nobody changed.
`DONE` lines of the logbook stay in, so a completion does move it. It sees no
position: a moved block has the hash it had. A parent's hash does not see a new
child either, so deleting a block, which deletes what is under it, takes a
separate hash over the whole subtree.

The check sits in one module (`preconditions.py`) and runs on a read past the
cache, before `--dry-run` and before anything is written. Whether a call owes a
precondition is a mark on the command like the one for `read_only`, asked once
in the same place before the first request, so a command that gains the option
cannot forget to be asked, and a form of a command that changes no block it
read (`copy-block` without `--remove`) owes none. `require_preconditions`
turns the obligation on; it is off by default because the write outputs carry
no new hash yet, see [safety.md](safety.md#why-it-is-off-by-default-and-when-to-turn-it-on).
The refusal never prints the block's current hash: the next step is to read the
block and decide again, not to send the same change with a hash that now
matches.

## Query values are escaped in one place

Values entering datalog queries were interpolated with f-strings: one call site
escaped quotes but not backslashes, the rest escaped nothing, so a page name
could alter the query around it. The fix was a build layer
([`datalog.py`](../logseq_cli/datalog.py)) that every interpolating call site goes
through, rather than a patch at each site — scattered escaping is the kind of
thing that holds until the next call site is added and nobody remembers the
rule. `smart-query --advanced` stays a raw pass-through, because a documented
escape hatch is safer than one people invent for themselves.
See [0.9.0, Security](../CHANGELOG.md#090---2026-09-14).

## Analysis output is measured against a real graph

`analyze-graph` reported 438 open tasks for a graph with 256, because it
counted "todo" anywhere in any casing; the mood counters scored "nicht
erfolgreich" as positive, 16% of positive hits in a 90-day sample; and
`find-knowledge-gaps` reported 596 orphans that were mostly Logseq's own
by-products. None of that was caught by tests asserting that output exists —
it took reading the numbers next to a graph whose real answer was known. A
plausible number that gets believed is worse than an obvious failure, so these
commands now measure one defined thing each, and the tasks that
`analyze-graph`, `get-todos` and `smart-query` report come from the same
classification of `:block/marker`.
See [0.9.0, Fixed](../CHANGELOG.md#090---2026-09-14).

## Output is bounded because the consumer has a context limit

An agent reading a month of journals gets 431,996 characters, against about
30,000 that Claude Code passes on from a shell command — past that, the agent
gets a 2,000-character preview and a file it would have to read in parts, so
either the answer is a fragment or the reading costs the context it was meant
to save. So `--tail`
and `--limit` filter **before** fetching rather than after, which keeps the
omitted days from costing API calls as well. Truncation is never silent: the
count of omitted days goes to stderr while stdout stays pure payload.
See [0.6.0](../CHANGELOG.md#060---2026-08-07).

## A block reference is not readable on its own

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

## A task is where it stands, not only where it was written

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

## Exit status: done or not done, and no resume

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
[ADR 0004](../docs/adr/0004-a-non-zero-exit-means-not-done.md) records the
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

## A write takes its target by name, not from a pipe

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

## This is a tool for file-based graphs

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
