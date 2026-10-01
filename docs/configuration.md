# Configuration

The CLI runs without a config file. Reading pages, writing journal entries,
searching, moving blocks — none of it needs one.

A few things do, because they depend on how *your* graph is organised and the
code cannot guess them:

- which heading journal writes go under by default,
- short names for headings you write under often,
- the namespace that marks your project pages,
- the property that marks a person page,
- the words and tags `analyze-journal-patterns` looks for,
- whether a write at the end of a section goes before the empty blocks that end it.

The first four have no defaults: a command that needs one and does not find it
says so and exits non-zero. The fifth has English defaults that work out of the box and
silently find nothing in a journal written in another language, which is why it
is worth setting. The last is off until you turn it on.

Everything else is connection settings, and those live in environment
variables or flags (see [Environment variables and flags](#environment-variables-and-flags)).

Three example files sit in the repository root:

| File | For |
|------|-----|
| `config.example.toml` | An English graph, every option commented |
| `config.example.de.toml` | A German-language graph, same options |
| `config.example.minimal.toml` | Journal heading only |

Copy one, delete what you do not need, keep what you do.


## Environment variables and flags

| Variable | Default | Description |
|----------|---------|-------------|
| `LOGSEQ_HOST` | `127.0.0.1` | Logseq API host |
| `LOGSEQ_PORT` | `12315` | Logseq API port |
| `LOGSEQ_TOKEN` | (empty) | Bearer token for authentication; `--token` overrides it |
| `LOGSEQ_API_URL` | auto | Full API URL override |
| `LOGSEQ_JOURNAL_HEADING` | (none) | Default heading for journal writes: `add-journal-block`, `add-journal-content`, `add-block-ref` (e.g. `## Log`) |
| `LOGSEQ_CLI_CACHE_TTL` | `60` | In-memory read-cache TTL in seconds (0 = disabled). Per process, not shared between invocations |
| `LOGSEQ_CLI_RANGE_WORKERS` | `5` | Parallel workers for `get-journal-range` (1–16) |
| `LOGSEQ_CLI_CONFIG` | (none) | Path to a config file, overriding the default locations |
| `LOGSEQ_CLI_KEEP_EMPTY_BLOCKS_LAST` | (none) | `1`, `true`, `yes` or `on` turns on, `0`, `false`, `no` or `off` turns off, over `[graph] keep_empty_blocks_last` (see [`[graph] keep_empty_blocks_last`](#graph-keep_empty_blocks_last)); empty is not set, any other value counts as off with a warning |
| `LOGSEQ_CLI_READ_ONLY` | (none) | `1`, `true`, `yes` or `on` switches every command that writes off; `0`, `false`, `no`, `off` and empty do nothing; any other value switches it on with a warning. Only tightens (see [`[safety] read_only`](#safety-read_only)) |

All connection settings can also be passed as CLI flags: `--host`, `--port`, `--token`. `--read-only` is a flag too, not a connection setting: see [`[safety] read_only`](#safety-read_only).

### Journal heading

By default, `add-journal-block` appends blocks at the top level of the journal page. Set `LOGSEQ_JOURNAL_HEADING` to automatically insert blocks under a specific heading:

```bash
# In your shell profile (~/.zshrc, ~/.bashrc, etc.)
export LOGSEQ_JOURNAL_HEADING="## Log"
```

This can be overridden per call:

```bash
# Use a different heading
logseq-cli add-journal-block --under-heading "## Notes" --content "..."

# Force top-level (ignore env var)
logseq-cli add-journal-block --top-level --content "..."
```

## Where the file is looked for

In this order, first hit wins:

1. `$LOGSEQ_CLI_CONFIG` — when set, **only** this path is considered. A
   missing file at that path is an error for a command that writes and a
   warning for one that only reads (see below).
2. `$XDG_CONFIG_HOME/logseq-cli/config.toml`, or
   `~/.config/logseq-cli/config.toml` when `XDG_CONFIG_HOME` is unset.
3. `~/.logseq-cli.toml`.

No file anywhere is normal and silent. A file that exists but is unreadable or
is not valid TOML raises an error instead — you meant to configure something,
so failing quietly would hide the mistake.

Errors from this layer come back like every other CLI error: a message on
stderr, a non-zero exit, and with `--json` a `"reason": "config_error"` so a script can
tell a broken setting apart from a broken connection.

If `LOGSEQ_CLI_CONFIG` is set but the file it names is gone — deleted, renamed,
or a typo in the path — a command that only reads and looks at the config
prints a warning to stderr and carries on without it (commands that never read
a setting do not look, and say nothing): a stale variable should not stop commands that
need no settings, and a command that does need one still fails loudly, naming
the setting. A command that writes refuses (`reason: config_error`, `--dry-run`
included): the file may hold the limit in [`[safety]`](#safety-read_only), and
the variable is the one way to name it. The same holds for a file that does not
parse: a command that writes refuses, one that only reads reports it where it
needs a setting.

Passing a path explicitly is different: that is an instruction, so a file that
is not there is an error.


## Precedence

Highest first:

```
command-line flag  >  environment variable  >  config file  >  built-in default
```

For the journal heading that reads: `--under-heading` / `--top-level` beats
`LOGSEQ_JOURNAL_HEADING`, which beats `[journal] default_heading`, and with
none of them set, entries go to the top level of the page.

`LOGSEQ_JOURNAL_HEADING` predates the config file and stays authoritative, so
existing shell profiles keep working unchanged after you add a config file.

One rule breaks the order: [`[safety] read_only`](#safety-read_only) only
tightens. A config that says `true` is not switched off by an environment
value of `false` or by leaving out `--read-only`; the flag or the environment
can switch it on when the config says nothing.

## The options

### `[journal] default_heading`

The heading journal writes go under when you pass neither `--under-heading`
nor `--top-level`.

```toml
[journal]
default_heading = "## Log"
```

Applies to `add-journal-block`, `add-journal-content` and `add-block-ref`.

**Without it:** entries are appended at the top level of the journal page.
That is the behaviour of the CLI before any config existed.

### `[journal.headings]`

Short names for headings, so you can type `--under-heading tasks` instead of
`--under-heading "## Tasks"`.

```toml
[journal.headings]
log = "## Log"
tasks = "## Tasks"
meeting = "## Meetings"
```

**Without it:** `--under-heading` always takes the literal heading text, which
keeps working either way — a value that is not a key in this table is passed
through unchanged.

**Spelling has to match exactly.** Heading matching ignores decoration — a
trailing renderer macro (`## Tasks {{renderer :todomaster}}`) and block
properties on the lines below (`id::`, `collapsed::`) are stripped before
comparing, and runs of whitespace collapse. It does **not** ignore case:
`## Log` and `## log` are two different headings, in Logseq as much as here.

That matters because of what happens on a miss. `--under-heading` creates the
heading when it cannot find it, so a wrong capitalisation does not produce an
error — it silently adds a second heading next to the one you meant, and writes
underneath that. Nothing warns you.

So copy the value out of your graph rather than typing it from memory; see
[Finding your own values](#finding-your-own-values) for the command that prints
your headings as they are actually stored.

### `[graph] projects_namespace`

The namespace prefix that marks your project pages, e.g. `projects/Project
Alpha` or `vorhaben/Gartenhaus`.

```toml
[graph]
projects_namespace = "projects/"
```

Used by `smart-query --request "projects"` only.

**Without it:** that one query reports the missing setting and exits non-zero:

```
smart-query --request 'projects' needs 'projects_namespace' under [graph] in your config.
  expected in: ~/.config/logseq-cli/config.toml
  see config.example.toml in the repository for the format.
  this setting describes your own graph, so there is no default.
```

It does not guess a prefix and return an empty list, because an empty list is
indistinguishable from "you have no projects". Every other command is
unaffected.

### `[graph] person_property` and `[graph] person_value`

The page property that marks a person page, and the value it must carry. A
page with `type:: Person` matches the pair below:

```toml
[graph]
person_property = "type"
person_value = "Person"
```

Used by `smart-query --request "persons"` only. Both are required — the
property name alone does not identify a person page.

**Without them:** same as above, the query names the missing setting and exits
non-zero. Nothing else changes.

### `[graph] keep_empty_blocks_last`

Whether a write at the end of a section goes before the empty blocks that end
it, so they stay last.

```toml
[graph]
keep_empty_blocks_last = true
```

Many Logseq users keep an empty block at the end of a section as a place to
click and type. The CLI appends as the last child, so every entry it writes
lands behind that block and an empty line stays in front of it. With this on,
the write goes directly before the empty blocks at the end. Given `TODO a` and
an empty block, `insert-block --child-of <section> --content "TODO b"` leaves
`TODO a`, `TODO b` and the empty block.

- **Nothing is overwritten or deleted.** The empty block is never written to,
  so one you are typing in, or one another writer has just filled, is untouched;
  the cursor can stay in it, except with `--keep-ids`, which refuses any write
  while a block is open, as it always did. The empty blocks stay last.
- **One command is one write.** Every block of a command (a second `--content`,
  the second root of a tree) goes in order directly before the empty blocks.
- **What counts as empty:** no text, no properties and no children. An empty
  block with an empty child is not empty: the child was put there on purpose.
  Empty blocks in the middle of a section are left alone.
- **Which writes:** the ones that end a section, a last child or the end of a
  page: `insert-block --child-of` (not `--first`) and `--page`, `add-note-content`,
  `add-journal-block`, `add-journal-content`, `add-block-ref`, the root of
  `copy-block`, and a heading that has to be created. Not `--after`, `--before`,
  `--first`, and not the deprecated `add-journal-entry`.
- **`--keep-ids`** works as it does for `--before`: the ids are kept and the
  blocks go before the empty blocks.
- **Page properties.** A block of nothing but `key::` lines is not put before
  the first block of a page: Logseq would read it as the page's properties, and
  `title::` renames the page. It is appended as it would be without the setting.
- `--dry-run` names the block the write would go before (`would go before the
  empty block ...`; `would_go_before_empty_block` in JSON). After a write that
  did, the JSON has `before_empty_block`.

**Without it:** writes append as they always did. This is the default, because an
empty last block can be deliberate, as a spacer or a template's placeholder.

The environment variable `LOGSEQ_CLI_KEEP_EMPTY_BLOCKS_LAST` overrides the file in
both directions (`1`, `true`, `yes`, `on` or `0`, `false`, `no`, `off`); an empty
one is not set, and any other value counts as off, with a warning. A value in the
file that is not `true` or `false` is a config error.

### `[analysis]`

What `analyze-journal-patterns` looks for when it scores moods and counts
project mentions. Unlike the settings above, these have defaults — English
ones — so the command runs either way. In a journal written in another
language it simply finds nothing, and a journal with no moods in it looks
exactly the same, which is why this is worth setting rather than assuming it
works.

```toml
[analysis]
mood_positive = ["happy", "great", "productive", "grateful"]
mood_negative = ["tired", "stressed", "frustrated", "overwhelmed"]

# Labels for an explicit mood line, as in `mood: good`.
mood_labels = ["mood", "feeling"]

# Projects, written as a namespace. Both `#projects/alpha` and
# `[[projects/alpha]]` count — a graph that namespaces its project pages
# usually contains both spellings.
project_tag_prefix = "#projects/"

# For graphs that tag flatly instead (`#alpha`, `#beta`): those names cannot
# be derived from a prefix, so list them.
project_tags = ["alpha", "beta"]
```

Words are matched whole and case-insensitively, and are escaped before they
reach the pattern — a word may contain regex characters without surprising
you. An empty list matches nothing rather than everything.

**The mood words judge stated moods, not the whole journal.** A line has to
say one — `mood: good`, `stimmung: mies`, with the label coming from
`mood_labels` — and the word lists then decide whether that value is positive
or negative. Counting every occurrence instead measured how often such words
appear in technical prose: "nicht erfolgreich" and "schmeckt nicht gut" both
counted as positive, because a word list cannot see a negation. If you never
write a mood line, the counts stay at zero, which is the honest answer.

**Without it:** the English defaults apply and `#project/` is assumed. Measured
against a German journal of 120 days: 3 mood hits with the defaults, 148 with a
German list.

### `[safety] read_only`

```toml
[safety]
read_only = true
```

| | |
|---|---|
| Values | `true` or `false` (a TOML boolean); default `false` |
| Effect | every command that writes refuses before its first request, `--dry-run` included, with `reason: read_only`; commands that only read, `--help` and `init` run as before |
| Also set by | `LOGSEQ_CLI_READ_ONLY` and `--read-only`; all three only tighten, there is no way to switch a `true` off from outside the file |
| Shown by | `logseq-cli doctor`, every run: `read_only: on (config …)` or `off (no [safety] in …)` or `off (no config file found)`, and `unknown, commands that write refuse until this is fixed: …` when the config cannot be used; it does not turn a healthy check red |

`[safety]` is the one section checked strictly. An unknown key
(`readonly = true`), a `[safety]` key outside `[safety]` (at the top level, in
another section, nested as `[journal.safety]`, or under a misspelt `[saftey]`) and a value that is not a
boolean are errors that name the key and, where there is one, the key meant:

```text
Error: …/config.toml: unknown key `readonly` in [safety], did you mean `read_only`?
```

A command that writes refuses with `reason: config_error`; one that only
reads runs, mostly without a word about it (`doctor` shows it every time), so a slip in `[safety]` does not stop your reads. Other
sections stay tolerant: an unknown key in them is ignored, and `doctor` names a
section it does not read.

`init` writes to the config file that is in use, so with your config in
`~/.logseq-cli.toml` it does not create a second file under `~/.config/` that
would be found first. Since that file exists, `init --force` is needed, and it
keeps the `[safety]` section; it refuses a file it cannot parse.

How binding each way of setting it is, and what it does not stop, is in
[safety.md](safety.md).

## When a section does not exist in your graph

Graphs differ. The answer is almost always "leave the setting out", and the
paragraphs below say what happens then.

### My journal has no headings at all

Leave `[journal]` out entirely, or drop `default_heading` from it. Journal
entries are then appended at the top level of the page — which is exactly what
the CLI did before config files existed.

If you have `LOGSEQ_JOURNAL_HEADING` exported in a shell profile, unset it too;
it wins over the config file, so the config alone cannot turn it off. For a
single call, `--top-level` ignores both.

### I have no project pages in a namespace

Leave `projects_namespace` out. The consequence is scoped to one query:
`smart-query --request "projects"` prints the message shown above and exits non-zero.

It fails rather than returning nothing on purpose. A guessed prefix would
produce an empty result that looks exactly like a graph without projects, and
you would go looking for the wrong bug. All other commands — including every
other `smart-query` request — work normally without this setting.

### I mark projects differently, e.g. by a property

`projects_namespace` matches on the page *name* prefix, so it cannot express
`type:: project`. There is no property-based project setting, and leaving
`projects_namespace` out costs you nothing but that one `smart-query` request.

Use `query-pages-by-property` instead — it needs no config at all:

```bash
logseq-cli --token "TOKEN" query-pages-by-property --key "type" --value "project"

# Omit --value to list every page carrying the key, with its values
logseq-cli --token "TOKEN" query-pages-by-property --key "type"
```

For anything the built-in templates do not cover, `smart-query --advanced`
passes a raw Datalog query through untouched:

```bash
logseq-cli --token "TOKEN" smart-query --advanced \
  --request '[:find (pull ?p [*]) :where [?p :block/name] [?p :block/properties ?props] [(get ?props :type) ?t] [(= ?t "project")]]'
```

If you want to see the query a template generated — the namespace variant
included — add `--include-query` to `smart-query`.

### I configure a heading my journal does not have yet

That is fine. `--under-heading` creates the heading when the journal page does
not have it — the option's own help says "Creates heading if missing". The
same applies to a `default_heading` that only exists in your config so far: the
first write creates it, subsequent writes append under it.

The flip side is that a *misspelled* heading is indistinguishable from a new
one. Write `## log` in your config while the journal contains `## Log`, and the
existing section is not found: a second, lowercase heading is created beside it
and your entries go there. No error, no warning — to Logseq those are simply
two different headings.

So take the spelling from the graph, not from memory. The heading command under
[Finding your own values](#finding-your-own-values) prints your headings exactly
as they are stored; copy one of those lines.

### I make a typo in a short name

Nothing resolves. Names from `[journal.headings]` are looked up; anything not
found is used verbatim as the heading text. So `--under-heading taks` writes
under a heading literally called `taks` (creating it, per the rule above) —
it never quietly falls back to `tasks`, `default_heading`, or top level.

The same holds for a typo in the *value*: matching tolerates decoration
(renderer macros, `id::`/`collapsed::` lines, extra whitespace) but not a
different capitalisation.

If entries turn up in an odd place, check the spelling first — the short name
and the heading it maps to. `--dry-run` on `add-journal-block` shows where a
write would land before it happens.

## Letting the CLI find them for you

```bash
logseq-cli --token "TOKEN" init --dry-run   # show what it would write
logseq-cli --token "TOKEN" init             # write it
```

`init` reads the graph — never writes to it — and proposes a config from what
it finds: the headings your recent journals use, the namespace most of your
pages sit under, the `type::` value that appears most. Every suggestion comes
with the count it rests on (`## Log  147/150`), because these are counts, not
certainties.

It looks at the most recent journals only (120 by default, `--days` to change
that). A section you stopped using years ago still sits in hundreds of old
files and would otherwise outrank the one you use now.

It writes to the config file that is in use, so a config in `~/.logseq-cli.toml`
gets no second file under `~/.config/logseq-cli/` that would be found first and
hide it. `--output PATH` writes there instead.

Two things it will not do: overwrite an existing config without `--force`, and
guess `[analysis]` — which words carry mood in your journal is not something a
count can tell.

If the answer looks wrong, the section below shows how to check it by hand.

## Finding your own values

Run these against your own graph folder — the directory holding `journals/`
and `pages/`.

**Which headings do your journals actually use?**

```bash
grep -rhoE "^[[:space:]]*-?[[:space:]]*#{1,4} .*" journals/*.md \
  | sed -E 's/^[[:space:]]*-?[[:space:]]*//' | sort | uniq -c | sort -rn | head
```

Each line is a count and a heading. The ones at the top are the sections you
really write under — good candidates for `default_heading` and for
`[journal.headings]`. Copy the heading text exactly as it appears, `##`
included, and with the same capitalisation: matching is case-sensitive, and a
heading that does not match is created rather than reported (see
[`[journal.headings]`](#journalheadings)). A trailing `{{renderer ...}}` may be
left off — that part is ignored when matching.

**Which namespaces exist?**

Logseq encodes the `/` of a namespaced page name in the filename. Depending on
the graph's age and settings that is `___` or `%2F`, so match both:

```bash
ls pages/ | grep -E '___|%2F' | sed -E 's/(___|%2F).*//' | sort | uniq -c | sort -rn
```

Each line is a count and a namespace prefix. A prefix with many pages under it
is what `projects_namespace` wants — written with a trailing slash, as it
appears in the page name (`projects/`), not as it appears in the filename.

If that returns nothing, ask the graph itself instead of the filenames:

```bash
logseq-cli --token "TOKEN" get-all-pages --json \
  | jq -r '.[] | .originalName // .name' | grep / | sed -E 's|/.*||' | sort | uniq -c | sort -rn
```

Capitalisation does not matter for this setting: the prefix is lowercased
before it is matched, so `Projects/` and `projects/` behave the same.

**Which properties do your pages use to classify themselves?**

```bash
grep -rhoE "^\s*-? *[a-zA-Z-]+:: .*" pages/*.md | sed -E 's/^\s*-? *//' | sort | uniq -c | sort -rn | head -30
```

Each line is a count and a `property:: value` pair. Look for the pair you use
on person pages — `type:: person`, `typ:: person`, `kind:: contact`, whatever
your convention is. The left half goes into `person_property`, the right half
into `person_value`.

To narrow it to one property:

```bash
grep -rhoE "^\s*-? *type:: .*" pages/*.md | sort | uniq -c | sort -rn
```

**Checking the result**

```bash
logseq-cli --token "TOKEN" doctor
```

`doctor` is read-only and checks the connection end to end: is anything
listening on the port, is a token supplied, does the API answer, is a graph
loaded. Each step is reported separately, so a failure names which one broke.
Exit 0 means ready to read. It also says whether writes are switched off
(`read_only`) and by what; that does not fail it, and the closing line says
"Ready to read; writes are off." when they are. A config file that does not
parse fails it, and the advice is to fix the file, never to remove it: removing
a file that holds a limit lifts the limit.

Once your config is in place, the fastest functional check is the query that
needs it:

```bash
logseq-cli --token "TOKEN" smart-query --request "projects"
```

Exit 0 with results means the namespace matches. A non-zero exit with a message
naming `projects_namespace` means the setting is missing. Exit 0 with no results means
the setting is there but the prefix matches no page — check the spelling
against the namespace listing above.

## Python and the TOML parser

The config file is TOML. Python 3.11 and newer parse it with the standard
library's `tomllib`; nothing to install.

On Python 3.10 — the oldest version this CLI supports — `tomllib` does not
exist, so `tomli` is pulled in automatically as a conditional dependency
(`tomli>=2.0; python_version < '3.11'` in `pyproject.toml`). A normal `pip
install` handles it.

If a config file is present and no parser can be found at all, the CLI says so
by name rather than ignoring the file.
