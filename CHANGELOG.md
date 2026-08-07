# Changelog

All notable changes to `logseq-cli` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
