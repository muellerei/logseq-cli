# Changelog

All notable changes to `logseq-cli` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Security

- Values entering datalog queries were interpolated via f-string: one call
  site half-escaped (quote but not backslash), the rest not at all, so a
  crafted page name or content string could alter the query. A new build
  layer (`logseq_cli/datalog.py`) provides `edn_string` (backslash-then-quote,
  closes the trailing-backslash bypass), `edn_keyword` (whitelist, rejects
  injection shapes) and `page_name_literal` (lowercases, since `:block/name`
  is stored lowercased). All interpolating call sites go through it.
  `smart-query --advanced` stays the documented raw pass-through.

### Fixed

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

### Removed

- The legacy tree inserters `insert_formatted_content` and
  `insert_block_tree` accepted a failed write (HTTP 200 + `null`) as
  success. No command called them anymore; all insert paths use the strict
  variants that abort on a silent write failure.

## [0.8.0] - 2026-08-22

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

- `add-journal-block --content-file DATEI` und `insert-block --tree-file DATEI`:
  Blockinhalt aus einer Datei statt aus `--content`/`--tree`. Zwei Probleme
  loest das:
  - **Shell-Quoting entfaellt.** `--content "$(cat datei)"` bricht an einem
    Apostroph im Text; der Reflex, dann alle Sonderzeichen zu entschaerfen,
    verstuemmelt Umlaute gleich mit. Der Datei-Pfad hat keine Shell dazwischen.
  - **Mehrere buendige `- `-Wurzeln sind erlaubt.** Inline lehnt der Guard sie
    ab, weil sie dort still zu EINEM Block mit rohen Newline-Bullets wuerden.
    Aus der Datei wird der gesamte Text als Baum geparst, buendige Bullets sind
    dort legitime Geschwister-Wurzeln mit eigenen Kindern.

  `--content-file` schliesst `--content` aus, `--tree-file` schliesst `--tree`
  und `--content` aus. Eine fehlende, leere, nicht lesbare oder nicht
  UTF-8-kodierte Datei bricht ab, bevor irgendetwas geschrieben wird.

### Fixed

Vier Wege, auf denen die gemeldete Blockzahl von den tatsaechlich
geschriebenen Bloecken abweichen konnte. Drei davon endeten mit Exit 0 und
einer Erfolgsmeldung fuer Text, der nie im Graph ankam. Gefunden beim
adversarischen Pruefen des neuen Datei-Pfads, drei sind aelter als er.

- **`--upsert-heading` verwarf alle Wurzeln ausser der ersten** und meldete
  trotzdem `count_blocks(tree)`. Eine Datei mit drei Wurzeln schrieb eine und
  meldete neun. Weitere Wurzeln werden jetzt als Geschwister eingefuegt, und
  die gemeldete Zahl stammt aus den tatsaechlich zurueckgegebenen UUIDs.
- **Der Upsert-Pfad schrieb nicht-strict** (`insert_block_tree`): ein stiller
  Schreibfehler wurde uebersprungen und die Soll-Zahl gemeldet. Laeuft jetzt
  ueber `insert_block_tree_with_uuids(strict=True)`.
- **`insert_formatted_content_with_uuids` hatte als einzige Insert-Helferin
  keinen strict-Vertrag.** Bei einer Page, die Logseq nicht geladen hat,
  antwortet jeder Append mit HTTP 200 + `null`; die `None`-UUIDs wurden
  mitgezaehlt und als `Added N block(s)` gemeldet. Betrifft
  `add-journal-block --top-level`, den Heading-Fallback,
  `add-journal-content` und `add-note-content`.
- **`insert_block_tree_at_page_top` zaehlte im Batch-Pfad pro `--content`-Wert
  neu ab 0**, sodass ein Fehler im zweiten Wert `Nothing was written` meldete,
  obwohl der erste bereits stand.

Zusaetzlich: Bricht ein Baum-Insert mitten drin ab, nennt die Meldung jetzt
die Zahl der bereits geschriebenen Bloecke statt `Nothing was written`. Es
gibt kein Rollback (die API bietet keins), und die alte Formulierung lud zu
einem Retry ein, der die Bloecke dupliziert haette.

### Changed

- Der Guard von `add-journal-block --content` nennt jetzt `--content-file` als
  vierten Ausweg. Fuer den Inline-Pfad bleibt er unveraendert scharf.
- `--content-file` zusammen mit `--no-preserve` bricht ab, statt die Hierarchie
  still zu einem Block zusammenzufalten. `--no-preserve` bleibt fuer
  `--content` unveraendert nutzbar.

## [0.6.0] - 2026-08-07

Ergebnis eines Audits gegen gaengige CLI-Konventionen (clig.dev, POSIX/grep,
Tool-Design-Empfehlungen). Alle Defaults bleiben unveraendert:
ohne die neuen Flags verhalten sich alle Kommandos wie bisher.

### Added

- `--dry-run` fuer `update-block`, `remove-block`, `copy-block` und
  `delete-page`. Diese vier Operationen kaskadieren oder ueberschreiben
  (`remove-block` nimmt alle Kinder mit, `copy-block --remove` loescht die
  Quelle), hatten aber bisher keine Vorschau. `remove-block --dry-run` meldet
  zusaetzlich die Zahl der Nachfahren, die mitgeloescht wuerden.
- Output-Begrenzung fuer die Journal-Lesepfade:
  `get-journal-range --tail N` (neueste N Tage), `--limit N` (aelteste N Tage)
  und `--heading X` (pro Tag nur eine Sektion); `get-journal-summary
  --no-content` (Volltexte weglassen, Datum/Zeichenzahl/Topics behalten).
  `--tail`/`--limit` filtern vor dem Abruf, ausgelassene Tage kosten keinen
  API-Call. Gemessen an einem realen Graph: Range ueber 30 Tage 431.996 ->
  136.289 Zeichen, Summary "this week" 143.733 -> 793 Zeichen.
- **`doctor`**: read-only Health-Check in einem Aufruf. Prueft Listener auf dem
  API-Port, Token, eine echte API-Antwort und ob ein Graph geladen ist. Trennt
  dabei die Faelle, die sonst manuell auseinanderzuhalten sind: Logseq laeuft
  nicht / laeuft, aber die HTTP-API ist aus / API antwortet, aber der Token wird
  abgelehnt / API und Token ok, aber kein Graph offen. Jeder Fall bekommt eine
  eigene Handlungsempfehlung (`remedy`, auch im JSON). Exit 0 = les- und
  schreibbereit, 1 = nicht. Anlass: am 2026-08-05 lief der Logseq-Prozess,
  aber nichts lauschte auf Port 12315 - die Klaerung kostete sieben manuelle
  Diagnoseschritte.
- `delete-block` als Alias auf `remove-block`. Der Name ist die haeufigste
  Fehlannahme, weil `delete-page` danebensteht.
- `fail()`-Helper: bei gesetztem `--json` werden Fehler als JSON-Objekt
  ausgegeben, sonst als Klartext. In beiden Faellen ausschliesslich auf
  stderr, damit stdout den Nutzdaten vorbehalten bleibt.

### Fixed

- **Stille Schreibfehler werden nicht mehr als Erfolg gemeldet.**
  `insert_block_tree_with_uuids()` hatte `strict=False` als Default: Logseq
  beantwortet einen fehlgeschlagenen Insert mit HTTP 200 + `null`, die Funktion
  legte daraufhin eine `None`-UUID ab, uebersprang die Kinder des Blocks - und
  das Kommando meldete "Added N block(s)" mit Exit 0, obwohl nichts geschrieben
  wurde. Bei einem Journal-Eintrag heisst das: der Text ist weg und nichts sagt
  es. Betroffen waren fuenf von acht Aufrufern, darunter `add-journal-block`
  und `add-note-content` (die Schwesterfunktion
  `insert_block_tree_as_siblings` hatte bereits `strict=True`; die
  Inkonsistenz war unbeabsichtigt).
  `strict` ist jetzt Default; `insert_block_tree_at_page_top()` prueft den
  Top-Level-Append ebenfalls ueber `require_insert()`. `strict=False` bleibt
  fuer Aufrufer verfuegbar, die Teilschreibungen bewusst tolerieren.

- **`get-properties` meldete faelschlich "No properties".** Der Befehl las nur
  `page_data["properties"]`, Logseq legt Page-Properties aber auf dem ersten
  Block ab (dem Property-Block), wenn sie per `set-property` geschrieben
  wurden - dort blieb das Page-Objekt leer. Ergebnis: intakte Properties wurden
  als nicht vorhanden gemeldet, was `set-property` so aussehen liess, als haette
  es still versagt. Genau das steht als Symptom in der Projekt-Doku ("Properties
  kaputt", "Reparatur nur per delete-page + Neuaufbau") - tatsaechlich war es
  ein Lesefehler, kein Datenverlust. Jetzt mit Fallback auf den ersten Block;
  liefert das Page-Objekt Properties, bleibt es beim bisherigen Pfad ohne
  Zusatz-Call.

### Changed

- `delete-page` entscheidet die Bestaetigung jetzt ueber `sys.stdin.isatty()`
  statt ueber `--json`. Bisher wirkte `--json` als impliziter Force-Schalter;
  ein Skript kann JSON aber rein zur Datenverarbeitung anfordern. Interaktiv
  wird gefragt, nicht-interaktiv ist `--force` Pflicht (sonst Exit 1).
  Trennt Ausgabeformat von Sicherheitsbestaetigung.
- `get-page` liefert Exit 1, wenn eine angeforderte Seite nicht existiert
  (Ausgabe `(page does not exist)`, im JSON `"exists": false`). Eine
  existierende leere Seite bleibt Exit 0 mit `(empty page)`. Bisher waren
  beide Faelle ununterscheidbar. Batch-Reads geben weiterhin alle vorhandenen
  Seiten aus und melden den Fehler erst am Ende.
- Gekuerzte Journal-Ergebnisse melden auf stderr, wie viele Tage ausgelassen
  wurden (`showing N of M ... K omitted`). Ohne Kuerzung kein Hinweis.

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

Initial baseline (tagged `baseline-2026-05-06`). 30 commands across pages,
journals, blocks, search, properties, page management, and graph analysis.
