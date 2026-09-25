import datetime
import os
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

import click

from logseq_cli.blockprops import kept_properties
from logseq_cli.blocktext import refuse_split_heading, refuse_split_tree, without_block_ids
from logseq_cli.cliinput import content_or_file, read_content_file, require_content
from logseq_cli.config import load_config, resolve_heading
from logseq_cli.dates import (
    format_journal_date,
    journal_day_to_date,
    parse_date_keyword,
    parse_date_range,
)
from logseq_cli.group import cli
from logseq_cli.headings import find_or_create_heading, normalize_heading, strip_title_heading
from logseq_cli.ids import (
    BESIDES_IDS,
    BlockIdError,
    check_block_ids,
    collect_block_ids,
    require_text_besides_ids,
    tree_without_block_ids,
    without_block_ids_noted,
)
from logseq_cli.lookup import get_page_content
from logseq_cli.outlinetext import (
    contains_hierarchical_content,
    count_blocks,
    has_mixed_indentation,
    normalize_indentation,
    note_quote_breaks,
    outline_text,
    parse_hierarchical_content,
)
from logseq_cli.output import fail, handle_connection_error, json_text, output, uuid_fields
from logseq_cli.render import (
    blocks_to_markdown,
    bound,
    count_unresolved_refs,
    extract_page_links,
    extract_section,
    first_uuids,
    process_blocks,
    resolve_refs_in_blocks,
)
from logseq_cli.strictinsert import (
    append_in_page,
    insert_block_at,
    insert_block_tree_as_siblings,
    insert_block_tree_at_page_top,
    insert_block_tree_with_uuids,
    insert_tree_at_page_end,
    require_insert,
)


@cli.command("get-journal-summary", epilog="""\b
Examples:
  logseq-cli --token TOKEN get-journal-summary --range "this week"
  logseq-cli --token TOKEN get-journal-summary --range "last month" --no-content
Note:
  Despite the name this embeds each day's FULL text by default, so a month-long
  range is large. --no-content drops the bodies and keeps dates + topics +
  top concepts, which is what an overview usually needs.
  For raw block content use get-journal-range (supports --tail/--heading).
""")
@click.option("--range", "date_range", default="today", help="Date range: today, this week, last 30 days, this month, this year")
@click.option("--no-content", "no_content", is_flag=True, help="Omit per-day body text; keep dates, topics and top concepts")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
@handle_connection_error
def get_journal_summary(ctx, date_range, no_content, as_json):
    """Summarize journal entries within a date range."""
    api = ctx.obj["api"]
    start, end = parse_date_range(date_range)
    pages = api.get_all_pages()

    journal_entries = []
    all_topics = Counter()

    for page in pages:
        jd = page.get("journalDay") or page.get("journal-day")
        if not jd:
            continue
        try:
            d = journal_day_to_date(jd)
        except (ValueError, TypeError):
            continue

        dt = datetime.datetime.combine(d, datetime.time())
        if start <= dt <= end:
            page_name = page.get("originalName") or page.get("name", "")
            content = get_page_content(api, page_name)
            topics = extract_page_links(content)
            all_topics.update(topics)
            entry = {
                "date": format_journal_date(d),
                "page": page_name,
                "topics": topics,
            }
            # Keep the character count even when the body is dropped, so the
            # caller can see how much was withheld and re-fetch deliberately.
            if no_content:
                entry["content_length"] = len(content or "")
            else:
                entry["content"] = content
            journal_entries.append(entry)

    journal_entries.sort(key=lambda e: e["date"])
    top_concepts = all_topics.most_common(10)

    result = {
        "range": date_range,
        "entries_count": len(journal_entries),
        "entries": journal_entries,
        "top_concepts": [{"topic": t, "count": c} for t, c in top_concepts],
    }
    if no_content:
        result["content_omitted"] = True

    if as_json:
        output(result, True)
    else:
        click.echo(f"Journal Summary ({date_range}): {len(journal_entries)} entries\n")
        for entry in journal_entries:
            if no_content:
                topics = entry.get("topics") or []
                topic_str = f" — {', '.join(topics[:8])}" if topics else ""
                click.echo(f"--- {entry['date']} ({entry['content_length']} chars){topic_str}")
                continue
            click.echo(f"--- {entry['date']} ---")
            click.echo(entry["content"] or "(empty)")
            click.echo()
        if top_concepts:
            click.echo("Top Concepts:")
            for topic, count in top_concepts:
                click.echo(f"  {topic}: {count}")

@cli.command("get-journal-range", epilog="""\b
Examples:
  logseq-cli --token TOKEN get-journal-range --from 2026-04-20 --to 2026-04-26 --resolve-refs
  logseq-cli --token TOKEN get-journal-range --from 2026-01-01 --to 2026-04-30 --tail 5
  logseq-cli --token TOKEN get-journal-range --from 2026-04-01 --to 2026-04-30 \\
                                              --heading "## Log" --tail 7
  LOGSEQ_CLI_RANGE_WORKERS=10 logseq-cli --token TOKEN get-journal-range --from 2026-01-01 --to 2026-04-30
Notes:
  Output grows with the range - a month of journals is large. Narrow it with
  --tail N (newest N days), --limit N (oldest N days) and/or --heading "## Log".
  --tail/--limit apply BEFORE fetching, so skipped days cost no API calls.
  --max-chars cuts the blocks so the output fits, measured in the format
  printed (a block in --json is several times its text), oldest day first,
  between blocks. Days past the cut are not printed; stderr names them and the block
  to continue from with --from-block, or, for a block that does not fit, the
  --max-chars it needs.
  Parallel pool (default 5 workers, 1-16 via LOGSEQ_CLI_RANGE_WORKERS).
  Always pass --resolve-refs if downstream parses ((uuid)) refs.
  Per-day errors embed as {error: "..."} per entry; the other days are still
  printed, and the call then fails, naming the days it could not read.
""")
@click.option("--from", "from_date", required=True, help="Start date (YYYY-MM-DD or 'today'/'yesterday'/'tomorrow', inclusive)")
@click.option("--to", "to_date", required=True, help="End date (YYYY-MM-DD or 'today'/'yesterday'/'tomorrow', inclusive)")
@click.option("--resolve-refs", is_flag=True, help="Inline ((uuid)) block references with their content")
@click.option("--tail", "tail", default=None, type=int, help="Only the newest N journal days of the range, 1 or greater (applied before fetching)")
@click.option("--limit", "limit", default=None, type=int, help="Only the oldest N journal days of the range, 1 or greater (applied before fetching)")
@click.option("--heading", default=None, help="Return only the section under this heading per day (e.g. '## Log')")
@click.option("--max-chars", "max_chars", type=int, default=None, help="Cut the blocks so the output fits in N characters, 1 or greater, oldest day first. The cut falls between blocks; what is withheld is reported on stderr and, with --json, as 'withheld'/'cut' fields on the day it fell in. Day headers are not cut")
@click.option("--from-block", "from_block", default=None, help="Start at this block, as named by a --max-chars note: days and blocks before it are skipped, its ancestors kept as context")
@click.option("--format", "output_format", type=click.Choice(["text", "markdown"]), default="text", help="Output format: text (default) or markdown")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
@handle_connection_error
def get_journal_range(ctx, from_date, to_date, resolve_refs, tail, limit, heading, max_chars, from_block, output_format, as_json):
    """Get full block content for all journal pages in a date range (inclusive).

    Returns one entry per journal day, with blocks and page name.
    Unlike get-journal-summary this returns raw blocks, not an aggregated summary.

    Example:
        logseq-cli get-journal-range --from 2026-04-20 --to 2026-04-24
        logseq-cli get-journal-range --from 2026-04-20 --to 2026-04-24 --resolve-refs --json
        logseq-cli get-journal-range --from 2026-04-01 --to 2026-04-30 --heading "## Log" --tail 7
    """
    api = ctx.obj["api"]

    # fail() rather than BadParameter: this command speaks --json, and Click's
    # refusal is a usage dump on stderr that no caller can parse. An agent
    # reading stderr as JSON got prose exactly where it expected an object.
    if tail is not None and tail < 1:
        fail("--tail must be 1 or greater.", as_json)
    if limit is not None and limit < 1:
        fail("--limit must be 1 or greater.", as_json)
    if tail is not None and limit is not None:
        fail("--tail and --limit are mutually exclusive.", as_json)
    if max_chars is not None and max_chars < 1:
        fail("--max-chars must be 1 or greater.", as_json)

    start = datetime.datetime.combine(parse_date_keyword(from_date), datetime.time())
    end = datetime.datetime.combine(parse_date_keyword(to_date), datetime.time())

    if start > end:
        fail("--from must be before or equal to --to.", as_json)

    pages = api.get_all_pages()

    targets = []
    for page in pages:
        jd = page.get("journalDay") or page.get("journal-day")
        if not jd:
            continue
        try:
            d = journal_day_to_date(jd)
        except (ValueError, TypeError):
            continue

        dt = datetime.datetime.combine(d, datetime.time())
        if start <= dt <= end:
            page_name = page.get("originalName") or page.get("name", "")
            targets.append((d, page_name))

    # Narrow BEFORE fetching: skipped days must not cost API calls.
    targets.sort(key=lambda t: t[0])
    total_days = len(targets)
    if tail is not None:
        targets = targets[-tail:]
    elif limit is not None:
        targets = targets[:limit]
    omitted = total_days - len(targets)

    try:
        worker_setting = int(os.getenv("LOGSEQ_CLI_RANGE_WORKERS", "5"))
    except ValueError:
        worker_setting = 5
    max_workers = min(16, max(1, worker_setting))

    def _fetch_one(target):
        d, page_name = target
        try:
            blocks = api.get_page_blocks_tree(page_name)
            if heading and blocks:
                blocks = extract_section(blocks, heading)
            if resolve_refs and blocks:
                resolve_refs_in_blocks(api, blocks)
            return {
                "date": d.strftime("%Y-%m-%d"),
                "page": page_name,
                "blocks": blocks or [],
            }
        except Exception as exc:
            return {
                "date": d.strftime("%Y-%m-%d"),
                "page": page_name,
                "blocks": [],
                "error": f"{type(exc).__name__}: {exc}",
            }

    if not targets:
        entries = []
    elif max_workers == 1 or len(targets) == 1:
        entries = [_fetch_one(t) for t in targets]
    else:
        entries = []
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(_fetch_one, t) for t in targets]
            for future in as_completed(futures):
                entries.append(future.result())

    entries.sort(key=lambda e: e["date"])
    # Counted before --max-chars can cut a failed day from view: the read did
    # not complete either way.
    unread = [e["date"] for e in entries if e.get("error")]
    fetched = len(entries)

    # Never truncate silently: a shortened result must not read as the full range.
    if omitted > 0:
        which = "newest" if tail is not None else "oldest"
        click.echo(
            f"Note: showing {len(entries)} of {total_days} journal day(s) "
            f"({which} {len(entries)}); {omitted} omitted. "
            f"Widen with --tail/--limit or drop the flag for the full range.",
            err=True,
        )

    def _render(entries):
        if as_json:
            return json_text(entries) + "\n"
        out = []
        for entry in entries:
            err = entry.get("error")
            withheld = entry.get("withheld")
            if output_format == "markdown":
                out.append(f"# {entry['page']}\n\n")
                if err:
                    out.append(f"!! ERROR: {err}\n\n")
                elif entry["blocks"]:
                    starts = entry["blocks"][0].get("uuid") == first_uuid.get(entry["page"])
                    out.append(blocks_to_markdown(entry["blocks"], page_start=starts) + "\n")
                elif withheld:
                    out.append(f"({withheld} block(s) withheld by --max-chars)\n\n")
                else:
                    out.append("(empty)\n\n")
            else:
                out.append(f"=== {entry['page']} ===\n\n")
                if err:
                    out.append(f"!! ERROR: {err}\n")
                elif entry["blocks"]:
                    out.append(process_blocks(entry["blocks"]) + "\n")
                elif withheld:
                    out.append(f"({withheld} block(s) withheld by --max-chars)\n")
                else:
                    out.append("(empty)\n")
                out.append("\n")
        return "".join(out)

    first_uuid = first_uuids(entries)
    try:
        entries, note = bound(entries, _render, max_chars, from_block, "day")
    except LookupError as exc:
        fail(str(exc), as_json)
    if note:
        click.echo(note, err=True)

    # After the cap: the warning speaks about what is printed.
    if not resolve_refs:
        total_refs = sum(count_unresolved_refs(e.get("blocks", [])) for e in entries)
        if total_refs > 0:
            click.echo(
                f"⚠️  {total_refs} unresolved block-ref(s) in output — "
                f"re-run with --resolve-refs to inline them.",
                err=True,
            )

    click.echo(_render(entries), nl=False)

    # A range with holes is not the range asked for. The days that were read
    # stay on stdout; exit 0 here read as a complete result (#93).
    if unread:
        fail(f"{len(unread)} of {fetched} journal day(s) could not be read: "
             f"{', '.join(unread)}.", as_json,
             reason="partial_read", days=unread)

@cli.command("add-journal-entry", epilog="""\b
DEPRECATED. Use add-journal-block instead — it auto-detects hierarchy and supports
--under-heading / --upsert-heading.
An id:: line in --content is dropped with a Note, as in add-journal-block.
""")
@click.option("--content", required=True, help="Content to add")
@click.option("--date", default=None, help="Date (YYYY-MM-DD), defaults to today")
@click.option("--as-block/--multi-block", default=True, help="Add as single block or split into multiple")
@click.option("--dry-run", is_flag=True, help="Report the target page and block count, without writing")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
@handle_connection_error
def add_journal_entry(ctx, content, date, as_block, as_json, dry_run):
    """Add a simple entry to a journal page (top-level only).

    Deprecated: Prefer add-journal-block which supports --under-heading.
    This command always appends at the top level of the page.

    Use --multi-block to split multi-line content into separate blocks.
    """
    click.echo("Note: add-journal-entry is deprecated. Use add-journal-block instead (supports --under-heading).", err=True)
    api = ctx.obj["api"]

    if date:
        d = parse_date_keyword(date)
    else:
        d = datetime.date.today()

    configs = api.get_user_configs()
    date_fmt = configs.get("preferredDateFormat") if configs else None
    page_name = format_journal_date(d, date_fmt)

    # Ensure journal page exists with journal property
    existing = api.get_page(page_name)
    content = strip_title_heading(content, page_name)
    # The blocks as they are written: checked to come back as one each (#47),
    # counted for the preview and written, from this one list.
    blocks = [content] if as_block else [ln.strip() for ln in content.split("\n") if ln.strip()]
    refuse_split_tree([{"content": block} for block in blocks], command="add-journal-entry")
    # An id:: line would become a block's uuid (#56); dropped, as every writer
    # drops it without --keep-ids, which this deprecated command does not get.
    # A line that was nothing but the id leaves no empty block behind, and
    # text that was nothing but ids is refused before the page is created.
    blocks, id_note = without_block_ids_noted(blocks)
    if id_note:
        if not as_block:
            blocks = [block for block in blocks if block.strip()]
        require_text_besides_ids("\n".join(blocks))
        click.echo(id_note, err=True)
    note_quote_breaks([{"content": block} for block in blocks])

    # Before the journal page is created: the preview must not be the one run
    # that leaves a page behind.
    if dry_run:
        planned = len(blocks)
        if as_json:
            output({"page": page_name, "date": str(d), "blocks_added": planned,
                    "would_create_page": not existing, "dry_run": True}, True)
        else:
            click.echo(f"[DRY RUN] Would add {planned} block(s) to journal: {page_name}")
            if not existing:
                click.echo(f"  page: {page_name} (would be created)")
        return

    if not existing:
        api.create_page(page_name, {"journal?": True})

    # Count what the graph actually took, not how many lines were handed in:
    # reporting len(lines) turned a partial write into "Added 3 block(s)" with
    # no hint that two are missing, which invites a retry that duplicates the
    # one that landed.
    if as_block:
        result = api.append_block_in_page(page_name, blocks[0])
        require_insert(result, f"a block on '{page_name}'")
        blocks_added = 1
    else:
        result = None
        written = 0
        for line in blocks:
            result = api.append_block_in_page(page_name, line)
            require_insert(result, f"a block on '{page_name}'", written_so_far=written)
            written += 1
        blocks_added = written

    if as_json:
        output({
            "page": page_name,
            "date": str(d),
            "blocks_added": blocks_added,
            "result": result,
        }, True)
    else:
        click.echo(f"Added {blocks_added} block(s) to journal: {page_name}")

@cli.command("add-journal-block", epilog="""\b
Examples:
  logseq-cli --token TOKEN add-journal-block --content "**$(date +%H:%M)** Meeting with [[Bob]]"
  logseq-cli --token TOKEN add-journal-block --date 2026-05-07 --content "**14:30** Nachtrag"
  logseq-cli --token TOKEN add-journal-block --under-heading "## Meeting" --content "..."
  logseq-cli --token TOKEN add-journal-block --content "TODO A" --content "TODO B"   # batch
  logseq-cli --token TOKEN add-journal-block --content-file entry.md                 # tree from file
  logseq-cli --token TOKEN add-journal-block --under-heading "## Meeting" \\
                                              --upsert-heading "### [[Carol]]" --content "..."
Notes:
  Default heading from LOGSEQ_JOURNAL_HEADING env (e.g. "## Log").
  --upsert-heading replaces a placeholder block under --under-heading without needing UUID.
  The replaced block keeps its properties, as with update-block; a property
  line in the new text is the new value of its key. A first block that is
  nothing but id:: lines is refused: it would empty the replaced block.
  Auto-detects tab-indented hierarchy in --content; no need to switch to add-journal-content.
  --content-file reads the whole file as ONE tree: flush "- " lines become
  sibling roots, tab-indented lines their children. No shell quoting, so
  apostrophes/quotes/umlauts are safe. Mutually exclusive with --content.
  --content without indentation is ONE block, and is refused if Logseq would
  read a line of it as a block of its own: a "- " or "# " line after the
  first, or a code fence nothing closes. In a closed code block such lines are
  fine. Indent sub-bullets for children, pass --content again for siblings.
  id:: lines are dropped unless --keep-ids is given, and the command says so;
  text that is nothing but id:: lines is refused.
  An id:: line inside a code block (``` or ~~~) is code and is written as is.
  --keep-ids restores an id only a ((ref)) still holds; it refuses, before
  writing anything, an id a block or page still has, one repeated in the
  content, and a second id:: line in one block.
  --keep-ids cannot be combined with --upsert-heading or --no-preserve.
  A quote ends at a blank line, and the paragraph after it shows as plain
  text: written anyway, with a Note on stderr. Start the blank line with ">"
  to keep the paragraph in the quote.
""")
@click.option("--content", "contents", multiple=True, help="Block content (repeatable for batch: --content 'text1' --content 'text2')")
@click.option("--content-file", "content_file", default=None, help="Read block content from a file and insert it as a tree (multiple flush '- ' roots allowed). Mutually exclusive with --content.")
@click.option("--date", default=None, help="Date (YYYY-MM-DD), defaults to today")
@click.option("--under-heading", default=None, help="Insert as child of this heading (e.g. '## Log'). Creates heading if missing. Default from LOGSEQ_JOURNAL_HEADING env var, or top-level if unset.")
@click.option("--upsert-heading", default=None, help="Find child block matching this heading under --under-heading and update it; insert as new block if not found.")
@click.option("--top-level", is_flag=True, help="Add as top-level block (ignore --under-heading and env var)")
@click.option("--preserve-formatting/--no-preserve", default=True, help="Preserve content formatting")
@click.option("--dry-run", is_flag=True, help="Show what would be written without making changes")
@click.option("--keep-ids", "keep_ids", is_flag=True, help="Keep the id:: values in the content instead of letting Logseq mint new ones, for moving or restoring an outline; an id only a ((ref)) still holds is restored, so the ref resolves again. Refused before any write: an id a block or page still has, a repeated or malformed one, two in one block")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
@handle_connection_error
def add_journal_block(ctx, contents, content_file, date, under_heading, upsert_heading, top_level, preserve_formatting, dry_run, keep_ids, as_json):
    """Add one or more blocks to a journal page.

    Pass --content multiple times for batch inserts under the same heading.
    This is the recommended command for journal entries. Inserts under the
    heading from LOGSEQ_JOURNAL_HEADING env var (default: top-level).

    Examples:
      logseq-cli add-journal-block --content "**14:30** Meeting notes"
      logseq-cli add-journal-block --under-heading "## Tasks" --content "TODO Task A" --content "TODO Task B"
      logseq-cli add-journal-block --date 2026-04-03 --content "Retroactive entry"
    """
    # --content and --content-file are mutually exclusive; exactly one is required.
    if content_file is not None and contents:
        raise click.UsageError("Specify either --content or --content-file, not both.")
    if content_file is None and not contents:
        raise click.UsageError("Missing option '--content' (or '--content-file').")

    # --content-file: the file IS the tree. Flush "- " roots are siblings here,
    # not the silent-failure case the guard below protects against, because the
    # whole text is parsed hierarchically instead of written as one raw block.
    from_file = content_file is not None
    if from_file:
        # --no-preserve collapses all whitespace, which would flatten the very
        # tree --content-file exists to insert (and leave raw "- " markers in
        # the text). The guard that catches this inline is skipped here, so the
        # combination must be rejected rather than silently written.
        if not preserve_formatting:
            raise click.UsageError(
                "--content-file and --no-preserve are incompatible: "
                "--no-preserve would collapse the hierarchy into ONE "
                "block.\n"
                "  - want the structure? -> drop --no-preserve\n"
                "  - want flowing text?  -> use --content"
            )
        contents = (read_content_file(content_file),)

    if not from_file:
        for c in contents:
            require_content(c)

    # --upsert-heading rewrites a block that already exists, and an existing
    # block's uuid cannot change. The flag would half work there, so the
    # combination is refused rather than honoured for some blocks only.
    if keep_ids and upsert_heading:
        raise click.UsageError("--keep-ids cannot be combined with --upsert-heading: "
                               "the block it replaces keeps its own uuid.")
    # --no-preserve joins the lines, and an id:: that is no longer on a line
    # of its own is no property: it would be written as text, silently.
    if keep_ids and not preserve_formatting:
        raise click.UsageError("--keep-ids cannot be combined with --no-preserve: "
                               "joining the lines turns id:: into plain text.")

    api = ctx.obj["api"]
    if date:
        d = parse_date_keyword(date)
    else:
        d = datetime.date.today()
    configs = api.get_user_configs()
    date_fmt = configs.get("preferredDateFormat") if configs else None
    page_name = format_journal_date(d, date_fmt)

    # What each value is written as, decided once: a tree when it carries
    # structure, otherwise one block (None here). The id check and the removal
    # run on exactly that, so they cannot disagree with the write. Checking a
    # flat value as a parsed outline, or removing lines from the text of a
    # tree, each let a check and a write see different blocks.
    # The text as it is written, prepared here once and not touched again: a
    # single value without --preserve-formatting is joined into one line, and
    # strip_title_heading also strips both ends. Checked before that, or
    # stripped again after, "id:: <uuid>\r" was no id and became one.
    joined = not preserve_formatting and len(contents) == 1
    contents = tuple(strip_title_heading(" ".join(c.split()) if joined else c, page_name)
                     for c in contents)
    trees = [parse_hierarchical_content(c)
             if preserve_formatting and (from_file or contains_hierarchical_content(c))
             else None
             for c in contents]

    blocks = [node for c, tree in zip(contents, trees)
              for node in (tree if tree is not None else [{"content": c, "children": []}])]
    # Every block as written must come back from the page file as that block
    # (#47). A flat value with a "- " line of its own is refused here, and one
    # that --no-preserve joins into a single line is not.
    refuse_split_tree(blocks, command="add-journal-block")

    if top_level:
        under_heading = None
    else:
        # A name from [journal.headings] resolves to its heading; anything else
        # is passed through, so a literal "## Log" keeps working. With no value
        # at all, the env var wins over the config's default_heading.
        under_heading = resolve_heading(load_config(), under_heading)
        # Before the id note below, which would otherwise sit ahead of the
        # refusal and break its JSON.
        refuse_split_heading(under_heading, command="add-journal-block")
    # Before the journal page is looked up and created: that is a write. And
    # before the id note below, like the heading check.
    if upsert_heading and not under_heading:
        click.echo("Error: --upsert-heading requires --under-heading", err=True)
        sys.exit(1)

    # Before the journal page is looked up or created: a refused id must leave
    # the graph untouched, and creating the page is a write too.
    try:
        note = check_block_ids(api, blocks, keep_ids)
    except BlockIdError as e:
        fail(str(e), as_json=as_json, **{e.field: e.ids})
    if note:
        trees = [tree_without_block_ids(t) if t is not None else None for t in trees]
        # The text is what flat values send, and what the previews show.
        contents = tuple(outline_text(t) if t is not None else without_block_ids(c)
                         for c, t in zip(contents, trees))
        # The first root replaces the matched block's text. One that was
        # nothing but its id would empty it, heading and all (#67). Refused
        # before the heading is looked up: that lookup may create it. The
        # upsert takes one value; a flat one is the whole content, which
        # check_block_ids refused already.
        if (upsert_heading and len(trees) == 1 and trees[0]
                and not trees[0][0]["content"].strip()):
            fail(f"The first block of the content {BESIDES_IDS}: with --upsert-heading "
                 "it replaces the matched block's text, which would be left empty. "
                 "Nothing was written.", as_json=as_json,
                 dropped_ids=collect_block_ids(blocks[:1]))
        click.echo(note, err=True)

    # For single content: unwrap to scalar for backward-compatible logic below
    if len(contents) == 1:
        content = contents[0]
    else:
        content = None  # Will be handled in batch path below

    # After the checks that can refuse, and on the text as written: dropped
    # id:: lines are gone from it.
    note_quote_breaks([node for c, t in zip(contents, trees)
                       for node in (t if t is not None else [{"content": c}])])

    # --- Batch path: multiple --content values ---
    if len(contents) > 1:
        existing = api.get_page(page_name)
        # Creating the journal page is a write, so it waits for the dry-run
        # check below: a preview that brings a page into existence is not a
        # preview. The flag is reported instead, because "the page does not
        # exist yet" is part of what the run would do.
        would_create_page = not existing
        if not existing and not dry_run:
            api.create_page(page_name, {"journal?": True})

        # Plan each --content value the same way for dry-run and live, so the
        # reported block count matches what is actually written (a value with
        # tab sub-bullets expands to a header + children, not one flat block).
        planned = []  # list of (kind, payload) where kind in {"tree", "flat"}
        any_hierarchical = False
        for c, tree in zip(contents, trees):
            if tree is not None:
                any_hierarchical = True
                planned.append(("tree", tree))
            else:
                if has_mixed_indentation(c):
                    c = normalize_indentation(c)
                planned.append(("flat", c))
        planned_total = sum(count_blocks(p) if k == "tree" else 1 for k, p in planned)

        if dry_run:
            if as_json:
                output({"page": page_name, "date": str(d), "blocks": planned_total,
                        "would_create_page": would_create_page, "contents": list(contents), "dry_run": True}, True)
            else:
                if any_hierarchical:
                    click.echo("Note: Hierarchical content detected, using structured insertion", err=True)
                click.echo(f"[DRY RUN] Would add {planned_total} block(s) to journal: {page_name}")
            if would_create_page:
                click.echo("  the journal page does not exist yet and would be created")
                for c in contents:
                    click.echo(f"  {c[:80]}")
            return

        heading_uuid = find_or_create_heading(api, page_name, under_heading) if under_heading else None
        if under_heading and not heading_uuid:
            click.echo(f"Warning: Could not find or create '{under_heading}', adding as top-level", err=True)
        uuids = []
        for kind, payload in planned:
            if kind == "tree":
                if heading_uuid:
                    uuids.extend(insert_block_tree_with_uuids(
                        api, payload, heading_uuid, strict=True, keep_ids=keep_ids,
                        _written=len(uuids)))
                else:
                    # payload is the parsed tree; insert top nodes + children at page level
                    uuids.extend(insert_block_tree_at_page_top(
                        api, payload, page_name, keep_ids=keep_ids, _written=len(uuids)))
            else:
                if heading_uuid:
                    r = insert_block_at(api, heading_uuid, payload, sibling=False,
                                        keep_ids=keep_ids, written_before=len(uuids))
                else:
                    r = append_in_page(api, page_name, payload, keep_ids,
                                       written_before=len(uuids))
                uuids.append(require_insert(r, "a journal block", written_so_far=len(uuids)))
        total = len(uuids)
        if any_hierarchical:
            click.echo("Note: Hierarchical content detected, using structured insertion", err=True)

        # From where the blocks went, not from what was asked: the heading may
        # not exist, and the blocks then went to the page (#93).
        if heading_uuid:
            position = f"under '{under_heading}'"
        elif under_heading:
            position = "top-level (heading not found)"
        else:
            position = "top-level"
        if as_json:
            output({"page": page_name, "date": str(d), "position": position, "blocks_added": total, **uuid_fields(uuids)}, True)
        else:
            click.echo(f"Added {total} block(s) to journal: {page_name} ({position})")
            if uuids:
                click.echo(f"  uuid: {uuids[0]}")
        return

    tree = trees[0]

    # Ensure journal page exists with journal property. Deferred when only
    # previewing: a dry run must not bring the page into existence.
    existing = api.get_page(page_name)
    would_create_page = not existing
    if not existing and not dry_run:
        api.create_page(page_name, {"journal?": True})

    # --- upsert-heading: find-or-replace child block under a heading ---
    if upsert_heading:
        if dry_run:
            position_desc = f"upsert '{upsert_heading}' under '{under_heading}'"
            if as_json:
                output({"page": page_name, "date": str(d), "position": position_desc, "content": content, "dry_run": True}, True)
            else:
                click.echo(f"[DRY RUN] Would upsert to journal: {page_name} ({position_desc})")
                click.echo(f"  {content[:120]}{'...' if len(content) > 120 else ''}")
            return

        heading_uuid = find_or_create_heading(api, page_name, under_heading)
        found_uuid = None
        if heading_uuid:
            heading_block = api.get_block(heading_uuid, include_children=True)
            children = heading_block.get("children", []) if heading_block else []
            target_norm = normalize_heading(upsert_heading)
            for child in children:
                if normalize_heading(child.get("content", "")) == target_norm:
                    found_uuid = child.get("uuid")
                    break

        if found_uuid:
            root_uuid = found_uuid
            # The first root of a tree replaces the matched block; its
            # children nest under it. Any further roots are siblings after it —
            # dropping them would lose content while still reporting
            # count_blocks(tree) as written.
            text = tree[0]["content"] if tree else content
            # The matched block keeps its properties, as with update-block (#67).
            _, kept = kept_properties(api, found_uuid, text)
            api.update_block(found_uuid, text, properties=kept or None)
            # Both inserts run strict and n counts the UUIDs actually
            # returned, plus 1 for the update. Using count_blocks(tree)
            # here would report the intended size even when a write
            # silently failed, which is the exact "text is gone and
            # nothing says so" case require_insert exists to prevent.
            n = 1
            if tree:
                kids = tree[0].get("children", [])
                if kids:
                    n += len(insert_block_tree_with_uuids(
                        api, kids, found_uuid, strict=True, _written=n))
                if len(tree) > 1:
                    n += len(insert_block_tree_as_siblings(
                        api, tree[1:], found_uuid, _written=n))
            status = "updated"
        elif heading_uuid:
            if tree is not None:
                created = insert_block_tree_with_uuids(api, tree, heading_uuid)
                n = len(created)
                root_uuid = created[0] if created else None
            else:
                r = api.insert_block(heading_uuid, content, {"sibling": False})
                n = 1
                root_uuid = require_insert(r, "the upsert block")
            status = "created"
        else:
            r = api.append_block_in_page(page_name, content)
            n = 1
            root_uuid = require_insert(r, f"a block on '{page_name}'")
            status = "top-level (heading not found)"

        position = f"upsert '{upsert_heading}' under '{under_heading}' ({status})"
        if as_json:
            output({"page": page_name, "date": str(d), "position": position, "blocks": n, **uuid_fields([u for u in [root_uuid] if u])}, True)
        else:
            click.echo(f"Added {n} block(s) to journal: {page_name} ({position})")
            if root_uuid:
                click.echo(f"  uuid: {root_uuid}")
        return

    # Auto-detect hierarchical content and delegate to structured insertion.
    # --content-file always takes this path: its flush "- " lines are roots,
    # which contains_hierarchical_content (indentation-based) would not detect.
    if tree is not None:
        click.echo("Note: Hierarchical content detected, using structured insertion", err=True)
        n = count_blocks(tree)
        position = f"under '{under_heading}'" if under_heading else "top-level"

        if dry_run:
            if as_json:
                output({"page": page_name, "date": str(d), "position": position, "blocks": n, "content": content, "dry_run": True}, True)
            else:
                click.echo(f"[DRY RUN] Would add {n} block(s) to journal: {page_name} ({position})")
            if would_create_page:
                click.echo("  the journal page does not exist yet and would be created")
                click.echo(f"  {content[:120]}{'...' if len(content) > 120 else ''}")
            return

        if under_heading:
            heading_uuid = find_or_create_heading(api, page_name, under_heading)
            if heading_uuid:
                uuids = insert_block_tree_with_uuids(api, tree, heading_uuid, keep_ids=keep_ids)
            else:
                click.echo(f"Warning: Could not find or create '{under_heading}', adding as top-level", err=True)
                uuids = insert_tree_at_page_end(api, page_name, tree, keep_ids=keep_ids)
                position = "top-level (heading not found)"
        else:
            uuids = insert_tree_at_page_end(api, page_name, tree, keep_ids=keep_ids)

        n = len(uuids)
        if as_json:
            output({"page": page_name, "date": str(d), "position": position, "blocks_added": n, **uuid_fields(uuids)}, True)
        else:
            click.echo(f"Added {n} block(s) to journal: {page_name} ({position})")
            if uuids:
                click.echo(f"  uuid: {uuids[0]}")
        return

    if dry_run:
        position_desc = f"under '{under_heading}'" if under_heading else "top-level"
        if as_json:
            output({"page": page_name, "date": str(d), "position": position_desc,
                    "content": content, "would_create_page": would_create_page,
                    "dry_run": True}, True)
        else:
            click.echo(f"[DRY RUN] Would add to journal: {page_name} ({position_desc})")
            if would_create_page:
                click.echo("  the journal page does not exist yet and would be created")
            click.echo(f"  {content}")
        return

    # Every branch checks its write. The hierarchical path already aborted on a
    # silent failure via require_insert; without the same check here a single
    # flat entry (the common case for a timestamped log line) was reported as
    # "Added block to journal" with exit 0 while nothing had been written.
    if under_heading:
        heading_uuid = find_or_create_heading(api, page_name, under_heading)
        if heading_uuid:
            result = insert_block_at(api, heading_uuid, content, sibling=False,
                                     keep_ids=keep_ids)
            position = f"under '{under_heading}'"
            _u = require_insert(result, f"a block under '{under_heading}'")
        else:
            result = append_in_page(api, page_name, content, keep_ids)
            position = "top-level (heading not found)"
            click.echo(f"Warning: Could not find or create '{under_heading}', added as top-level block", err=True)
            _u = require_insert(result, f"a block on '{page_name}'")
    else:
        result = append_in_page(api, page_name, content, keep_ids)
        position = "top-level"
        _u = require_insert(result, f"a block on '{page_name}'")

    if as_json:
        output({"page": page_name, "date": str(d), "position": position, "result": result, **uuid_fields([u for u in [_u] if u])}, True)
    else:
        click.echo(f"Added block to journal: {page_name} ({position})")
        # Before the preview: the content may itself contain "uuid: ...".
        if _u:
            click.echo(f"  uuid: {_u}")
        click.echo(f"  {content[:80]}{'...' if len(content) > 80 else ''}")

@cli.command("add-journal-content", epilog="""\b
Example:
  logseq-cli --token TOKEN add-journal-content \\
    --content "- ## Log\\n\\t- 14:30 Meeting [[Bob]]" --date $(date +%Y-%m-%d)
Note:
  Same heading logic as add-journal-block. Prefer add-journal-block for most cases —
  it now auto-detects hierarchy.
  id:: lines are dropped unless --keep-ids is given, and the command says so;
  text that is nothing but id:: lines is refused.
  An id:: line inside a code block (``` or ~~~) is code and is written as is.
  A code block stays one block: from a ``` or ~~~ line to the next such line
  without a bullet, every line is code. Without a bullet the fence goes on
  the block above; on a bullet line it is a block of its own. A fence
  nothing closes is refused: Logseq would let it swallow the blocks after it.
  --keep-ids restores an id only a ((ref)) still holds; it refuses, before
  writing anything, an id a block or page still has, one repeated in the
  content, and a second id:: line in one block.
  --content-file FILE is --content read from a file ('-' reads stdin), with
  the same rules; no shell quoting stands between the text and the command.
""")
@click.option("--content", default=None, help="Hierarchical content to add; this or --content-file is required")
@click.option("--content-file", "content_file", default=None, help="Read --content from this file instead ('-' reads stdin), so apostrophes, quotes and umlauts need no shell quoting. Mutually exclusive with --content")
@click.option("--date", default=None, help="Date (YYYY-MM-DD), defaults to today")
@click.option("--under-heading", default=None, help="Insert under this heading (e.g. '## Log'). Creates heading if missing. Default from LOGSEQ_JOURNAL_HEADING env var, or top-level if unset.")
@click.option("--top-level", is_flag=True, help="Add as top-level content (ignore --under-heading and env var)")
@click.option("--dry-run", is_flag=True, help="Show what would be written without making changes")
@click.option("--keep-ids", "keep_ids", is_flag=True, help="Keep the id:: values in the content instead of letting Logseq mint new ones, for moving or restoring an outline; an id only a ((ref)) still holds is restored, so the ref resolves again. Refused before any write: an id a block or page still has, a repeated or malformed one, two in one block")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
@handle_connection_error
def add_journal_content(ctx, content, content_file, date, under_heading, top_level, dry_run, keep_ids, as_json):
    """Add hierarchical (nested) content to a journal page.

    Use this for structured multi-block content with parent-child relationships.
    Content should use tab indentation for hierarchy:
      - ## Heading
      \\t- Child block
      \\t\\t- Grandchild block

    Heading resolution order (same as add-journal-block):
    1. --top-level flag -> always top-level
    2. --under-heading VALUE -> use that heading
    3. LOGSEQ_JOURNAL_HEADING env var -> use that heading
    4. No env var, no flag -> top-level (backward compatible)

    For single blocks, prefer add-journal-block instead.
    """
    content = content_or_file(content, content_file)
    require_content(content)

    if top_level:
        under_heading = None
    else:
        under_heading = resolve_heading(load_config(), under_heading)
    refuse_split_heading(under_heading, command="add-journal-content")

    api = ctx.obj["api"]

    if date:
        d = parse_date_keyword(date)
    else:
        d = datetime.date.today()

    configs = api.get_user_configs()
    date_fmt = configs.get("preferredDateFormat") if configs else None
    page_name = format_journal_date(d, date_fmt)
    content = strip_title_heading(content, page_name)

    # Before the journal page is created: a refused id must leave the graph
    # untouched. The tree checked is the tree written.
    tree = parse_hierarchical_content(content)
    refuse_split_tree(tree, command="add-journal-content")
    try:
        note = check_block_ids(api, tree, keep_ids)
    except BlockIdError as e:
        fail(str(e), as_json=as_json, **{e.field: e.ids})
    if note:
        click.echo(note, err=True)
        tree = tree_without_block_ids(tree)
        content = outline_text(tree)  # what the preview shows

    # Ensure journal page exists with journal property
    existing = api.get_page(page_name)
    if not existing and not dry_run:
        api.create_page(page_name, {"journal?": True})

    position = f"under '{under_heading}'" if under_heading else "top-level"

    if dry_run:
        n = count_blocks(tree)
        if as_json:
            output({"page": page_name, "date": str(d), "position": position, "blocks": n, "content": content, "dry_run": True}, True)
        else:
            click.echo(f"[DRY RUN] Would add {n} block(s) to journal: {page_name} ({position})")
            click.echo(f"  {content[:120]}{'...' if len(content) > 120 else ''}")
        return

    if under_heading:
        heading_uuid = find_or_create_heading(api, page_name, under_heading)
        if heading_uuid:
            uuids = insert_block_tree_with_uuids(api, tree, heading_uuid, keep_ids=keep_ids)
        else:
            click.echo(f"Warning: Could not find or create '{under_heading}', adding as top-level", err=True)
            uuids = insert_tree_at_page_end(api, page_name, tree, keep_ids=keep_ids)
            position = "top-level (heading not found)"
    else:
        uuids = insert_tree_at_page_end(api, page_name, tree, keep_ids=keep_ids)

    n = len(uuids)
    if as_json:
        output({"page": page_name, "date": str(d), "position": position, "blocks_added": n, "content_added": True, **uuid_fields(uuids)}, True)
    else:
        click.echo(f"Added {n} block(s) to journal: {page_name} ({position})")
        if uuids:
            click.echo(f"  uuid: {uuids[0]}")
