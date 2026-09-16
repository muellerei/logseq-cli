import datetime
import json
import os
import re
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from importlib import import_module
from importlib.metadata import version as _pkg_version, PackageNotFoundError
from pathlib import Path

import click
import requests

from logseq_cli.api import LogseqAPI, InvalidPortError
from logseq_cli.config import (
    ConfigError,
    config_search_paths,
    get,
    load_config,
    require,
    resolve_heading,
)
from logseq_cli.datalog import (
    edn_keyword,
    edn_string,
    page_name_literal,
)
from logseq_cli.helpers import (
    parse_repeater,
    next_occurrence,
    escape_regex,
    journal_day_to_date,
    format_journal_date,
    parse_date_keyword,
    parse_date_range,
    process_blocks,
    get_page_content,
    find_backlinks,
    parse_hierarchical_content,
    parse_tree_input,
    read_content_file,
    require_content,
    contains_hierarchical_content,
    reject_unsupported_multiline,
    MultilineContentError,
    has_flush_newline_bullets,
    has_mixed_indentation,
    normalize_indentation,
    find_heading,
    find_or_create_heading,
    PROPERTY_LINE_RE,
    insert_block_tree_with_uuids,
    insert_block_tree_as_siblings,
    insert_block_tree_as_first_children,
    insert_block_tree_at_page_top,
    collect_block_ids,
    invalid_block_ids,
    block_id_property,
    insert_formatted_content_with_uuids,
    block_uuid_from_result,
    require_insert,
    move_block_verified,
    find_blocks_by_content,
    resolve_single_block,
    parse_property_pairs,
    apply_block_properties,
    coerce_property_value,
    uuid_fields,
    normalize_heading,
    extract_page_links,
    extract_topics,
    strip_title_heading,
    is_journal_date,
    count_blocks,
)
from logseq_cli.group import cli, resolve_version
from logseq_cli.commands import edit  # noqa: F401  imported for registration
from logseq_cli.commands import pages  # noqa: F401  imported for registration
from logseq_cli.commands import analysis  # noqa: F401  imported for registration
from logseq_cli.commands import meta  # noqa: F401  imported for registration
from logseq_cli.commands import query  # noqa: F401  imported for registration
from logseq_cli.commands import properties  # noqa: F401  imported for registration
from logseq_cli.commands import todos  # noqa: F401  imported for registration
from logseq_cli.commands import blocks  # noqa: F401  imported for registration
from logseq_cli.output import fail, handle_connection_error, output
from logseq_cli.render import (
    BLOCK_REF_RE, blocks_to_markdown, blocks_with_ids, count_unresolved_refs,
    extract_backlink_names, extract_section, is_properties_block,
    resolve_refs_in_blocks,
)








# A text replacement must skip property lines: rewriting an id:: line breaks
# every ((block-ref)) to that block, irreversibly. Regex shared via helpers.

# find-block --with-children costs one extra read per match (the datalog pull
# carries no children), so the fan-out is capped and the remainder reported.












# ---------------------------------------------------------------------------
# 1. get-all-pages
# ---------------------------------------------------------------------------














# ---------------------------------------------------------------------------
# 2. get-page
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 3. get-block
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 3b. find-block
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 4. search-pages
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 5. get-backlinks
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 6. get-journal-summary
# ---------------------------------------------------------------------------
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




# ---------------------------------------------------------------------------
# 6b. get-journal-range
# ---------------------------------------------------------------------------
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
  Parallel pool (default 5 workers, 1-16 via LOGSEQ_CLI_RANGE_WORKERS).
  Always pass --resolve-refs if downstream parses ((uuid)) refs.
  Per-day errors embed as {error: "..."} per entry; range continues.
""")
@click.option("--from", "from_date", required=True, help="Start date (YYYY-MM-DD or 'today'/'yesterday'/'tomorrow', inclusive)")
@click.option("--to", "to_date", required=True, help="End date (YYYY-MM-DD or 'today'/'yesterday'/'tomorrow', inclusive)")
@click.option("--resolve-refs", is_flag=True, help="Inline ((uuid)) block references with their content")
@click.option("--tail", "tail", default=None, type=int, help="Only the newest N journal days of the range, 1 or greater (applied before fetching)")
@click.option("--limit", "limit", default=None, type=int, help="Only the oldest N journal days of the range, 1 or greater (applied before fetching)")
@click.option("--heading", default=None, help="Return only the section under this heading per day (e.g. '## Log')")
@click.option("--format", "output_format", type=click.Choice(["text", "markdown"]), default="text", help="Output format: text (default) or markdown")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
@handle_connection_error
def get_journal_range(ctx, from_date, to_date, resolve_refs, tail, limit, heading, output_format, as_json):
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

    # Never truncate silently: a shortened result must not read as the full range.
    if omitted > 0:
        which = "newest" if tail is not None else "oldest"
        click.echo(
            f"Note: showing {len(entries)} of {total_days} journal day(s) "
            f"({which} {len(entries)}); {omitted} omitted. "
            f"Widen with --tail/--limit or drop the flag for the full range.",
            err=True,
        )

    if not resolve_refs:
        total_refs = sum(count_unresolved_refs(e.get("blocks", [])) for e in entries)
        if total_refs > 0:
            click.echo(
                f"⚠️  {total_refs} unresolved block-ref(s) in output — "
                f"re-run with --resolve-refs to inline them.",
                err=True,
            )

    if as_json:
        output(entries, True)
    else:
        for entry in entries:
            err = entry.get("error")
            if output_format == "markdown":
                click.echo(f"# {entry['page']}\n")
                if err:
                    click.echo(f"!! ERROR: {err}\n")
                elif entry["blocks"]:
                    click.echo(blocks_to_markdown(entry["blocks"]))
                else:
                    click.echo("(empty)\n")
            else:
                click.echo(f"=== {entry['page']} ===\n")
                if err:
                    click.echo(f"!! ERROR: {err}")
                elif entry["blocks"]:
                    click.echo(process_blocks(entry["blocks"]))
                else:
                    click.echo("(empty)")
                click.echo()


# ---------------------------------------------------------------------------
# 7. analyze-graph
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 8. find-knowledge-gaps
# ---------------------------------------------------------------------------






# ---------------------------------------------------------------------------
# 9. analyze-journal-patterns
# ---------------------------------------------------------------------------














# ---------------------------------------------------------------------------
# 10. smart-query
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 11. suggest-connections
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 12. create-page
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 13. add-journal-entry
# ---------------------------------------------------------------------------
@cli.command("add-journal-entry", epilog="""\b
DEPRECATED. Use add-journal-block instead — it auto-detects hierarchy and supports
--under-heading / --upsert-heading.
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
    try:
        existing = api.get_page(page_name)
    except Exception:
        existing = None
    content = strip_title_heading(content, page_name)

    # Before the journal page is created: the preview must not be the one run
    # that leaves a page behind.
    if dry_run:
        planned = 1 if as_block else len(
            [l for l in content.split("\n") if l.strip()])
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
        result = api.append_block_in_page(page_name, content)
        require_insert(result, f"a block on '{page_name}'")
        blocks_added = 1
    else:
        lines = [l.strip() for l in content.split("\n") if l.strip()]
        result = None
        written = 0
        for line in lines:
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


# ---------------------------------------------------------------------------
# 14. add-journal-block
# ---------------------------------------------------------------------------
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
  Auto-detects tab-indented hierarchy in --content; no need to switch to add-journal-content.
  --content-file reads the whole file as ONE tree: flush "- " lines become
  sibling roots, tab-indented lines their children. No shell quoting, so
  apostrophes/quotes/umlauts are safe. Mutually exclusive with --content.
""")
@click.option("--content", "contents", multiple=True, help="Block content (repeatable for batch: --content 'text1' --content 'text2')")
@click.option("--content-file", "content_file", default=None, help="Read block content from a file and insert it as a tree (multiple flush '- ' roots allowed). Mutually exclusive with --content.")
@click.option("--date", default=None, help="Date (YYYY-MM-DD), defaults to today")
@click.option("--under-heading", default=None, help="Insert as child of this heading (e.g. '## Log'). Creates heading if missing. Default from LOGSEQ_JOURNAL_HEADING env var, or top-level if unset.")
@click.option("--upsert-heading", default=None, help="Find child block matching this heading under --under-heading and update it; insert as new block if not found.")
@click.option("--top-level", is_flag=True, help="Add as top-level block (ignore --under-heading and env var)")
@click.option("--preserve-formatting/--no-preserve", default=True, help="Preserve content formatting")
@click.option("--dry-run", is_flag=True, help="Show what would be written without making changes")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
@handle_connection_error
def add_journal_block(ctx, contents, content_file, date, under_heading, upsert_heading, top_level, preserve_formatting, dry_run, as_json):
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

    # Guard: reject flush (non-indented) newline bullets in ANY --content value.
    # Such content is neither detected as hierarchy (needs indentation) nor split
    # into siblings — it would silently become ONE block with raw "\n- " lines,
    # breaking the outline. Fail loudly with a fix instruction instead.
    # Skipped for --content-file, which always takes the structured path.
    if not from_file:
        for c in contents:
            require_content(c)
            try:
                reject_unsupported_multiline(
                    c, command="add-journal-block", accepts_tree=True
                )
            except MultilineContentError as e:
                raise click.UsageError(str(e))

    # For single content: unwrap to scalar for backward-compatible logic below
    if len(contents) == 1:
        content = contents[0]
    else:
        content = None  # Will be handled in batch path below

    if top_level:
        under_heading = None
    else:
        # A name from [journal.headings] resolves to its heading; anything else
        # is passed through, so a literal "## Log" keeps working. With no value
        # at all, the env var wins over the config's default_heading.
        under_heading = resolve_heading(load_config(), under_heading)

    # --- Batch path: multiple --content values ---
    if len(contents) > 1:
        api = ctx.obj["api"]
        if date:
            d = parse_date_keyword(date)
        else:
            d = datetime.date.today()
        configs = api.get_user_configs()
        date_fmt = configs.get("preferredDateFormat") if configs else None
        page_name = format_journal_date(d, date_fmt)
        try:
            existing = api.get_page(page_name)
        except Exception:
            existing = None
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
        for c in contents:
            c = strip_title_heading(c, page_name)
            if preserve_formatting and contains_hierarchical_content(c):
                any_hierarchical = True
                planned.append(("tree", parse_hierarchical_content(c)))
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
                click.echo(f"  the journal page does not exist yet and would be created")
                for c in contents:
                    click.echo(f"  {c[:80]}")
            return

        heading_uuid = find_or_create_heading(api, page_name, under_heading) if under_heading else None
        uuids = []
        for kind, payload in planned:
            if kind == "tree":
                if heading_uuid:
                    uuids.extend(insert_block_tree_with_uuids(
                        api, payload, heading_uuid, strict=True, _written=len(uuids)))
                else:
                    # payload is the parsed tree; insert top nodes + children at page level
                    uuids.extend(insert_block_tree_at_page_top(
                        api, payload, page_name, _written=len(uuids)))
            else:
                if heading_uuid:
                    r = api.insert_block(heading_uuid, payload, {"sibling": False})
                else:
                    r = api.append_block_in_page(page_name, payload)
                uuids.append(require_insert(r, "a journal block", written_so_far=len(uuids)))
        total = len(uuids)
        if any_hierarchical:
            click.echo("Note: Hierarchical content detected, using structured insertion", err=True)

        position = f"under '{under_heading}'" if under_heading else "top-level"
        if as_json:
            output({"page": page_name, "date": str(d), "position": position, "blocks_added": total, **uuid_fields(uuids)}, True)
        else:
            click.echo(f"Added {total} block(s) to journal: {page_name} ({position})")
        return

    api = ctx.obj["api"]

    if date:
        d = parse_date_keyword(date)
    else:
        d = datetime.date.today()

    configs = api.get_user_configs()
    date_fmt = configs.get("preferredDateFormat") if configs else None
    page_name = format_journal_date(d, date_fmt)

    # Ensure journal page exists with journal property. Deferred when only
    # previewing: a dry run must not bring the page into existence.
    try:
        existing = api.get_page(page_name)
    except Exception:
        existing = None
    would_create_page = not existing
    if not existing and not dry_run:
        api.create_page(page_name, {"journal?": True})

    if not preserve_formatting:
        # Collapse whitespace
        content = " ".join(content.split())

    content = strip_title_heading(content, page_name)

    # --- upsert-heading: find-or-replace child block under a heading ---
    if upsert_heading:
        if not under_heading:
            click.echo("Error: --upsert-heading requires --under-heading", err=True)
            sys.exit(1)

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
            if from_file or contains_hierarchical_content(content):
                tree = parse_hierarchical_content(content)
                if tree:
                    # The first root replaces the matched block; its children
                    # nest under it. Any further roots are siblings after it —
                    # dropping them would lose content while still reporting
                    # count_blocks(tree) as written.
                    #
                    # Both inserts run strict and n counts the UUIDs actually
                    # returned, plus 1 for the update. Using count_blocks(tree)
                    # here would report the intended size even when a write
                    # silently failed, which is the exact "text is gone and
                    # nothing says so" case require_insert exists to prevent.
                    api.update_block(found_uuid, tree[0]["content"])
                    n = 1
                    kids = tree[0].get("children", [])
                    if kids:
                        n += len(insert_block_tree_with_uuids(
                            api, kids, found_uuid, strict=True, _written=n))
                    if len(tree) > 1:
                        n += len(insert_block_tree_as_siblings(
                            api, tree[1:], found_uuid, _written=n))
                else:
                    api.update_block(found_uuid, content)
                    n = 1
            else:
                api.update_block(found_uuid, content)
                n = 1
            status = "updated"
        elif heading_uuid:
            if from_file or contains_hierarchical_content(content):
                tree = parse_hierarchical_content(content)
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
        return

    # Auto-detect hierarchical content and delegate to structured insertion.
    # --content-file always takes this path: its flush "- " lines are roots,
    # which contains_hierarchical_content (indentation-based) would not detect.
    if preserve_formatting and (from_file or contains_hierarchical_content(content)):
        click.echo("Note: Hierarchical content detected, using structured insertion", err=True)
        tree = parse_hierarchical_content(content)
        n = count_blocks(tree)
        position = f"under '{under_heading}'" if under_heading else "top-level"

        if dry_run:
            if as_json:
                output({"page": page_name, "date": str(d), "position": position, "blocks": n, "content": content, "dry_run": True}, True)
            else:
                click.echo(f"[DRY RUN] Would add {n} block(s) to journal: {page_name} ({position})")
            if would_create_page:
                click.echo(f"  the journal page does not exist yet and would be created")
                click.echo(f"  {content[:120]}{'...' if len(content) > 120 else ''}")
            return

        if under_heading:
            heading_uuid = find_or_create_heading(api, page_name, under_heading)
            if heading_uuid:
                uuids = insert_block_tree_with_uuids(api, tree, heading_uuid)
            else:
                click.echo(f"Warning: Could not find or create '{under_heading}', adding as top-level", err=True)
                uuids = insert_formatted_content_with_uuids(api, page_name, content)
                position = "top-level (heading not found)"
        else:
            uuids = insert_formatted_content_with_uuids(api, page_name, content)

        n = len(uuids)
        if as_json:
            output({"page": page_name, "date": str(d), "position": position, "blocks_added": n, **uuid_fields(uuids)}, True)
        else:
            click.echo(f"Added {n} block(s) to journal: {page_name} ({position})")
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
            result = api.insert_block(heading_uuid, content, {"sibling": False})
            position = f"under '{under_heading}'"
            _u = require_insert(result, f"a block under '{under_heading}'")
        else:
            result = api.append_block_in_page(page_name, content)
            position = "top-level (heading not found)"
            click.echo(f"Warning: Could not find or create '{under_heading}', added as top-level block", err=True)
            _u = require_insert(result, f"a block on '{page_name}'")
    else:
        result = api.append_block_in_page(page_name, content)
        position = "top-level"
        _u = require_insert(result, f"a block on '{page_name}'")

    if as_json:
        output({"page": page_name, "date": str(d), "position": position, "result": result, **uuid_fields([u for u in [_u] if u])}, True)
    else:
        click.echo(f"Added block to journal: {page_name} ({position})")
        click.echo(f"  {content[:80]}{'...' if len(content) > 80 else ''}")


# ---------------------------------------------------------------------------
# 15. add-journal-content
# ---------------------------------------------------------------------------
@cli.command("add-journal-content", epilog="""\b
Example:
  logseq-cli --token TOKEN add-journal-content \\
    --content "- ## Log\\n\\t- 14:30 Meeting [[Bob]]" --date $(date +%Y-%m-%d)
Note:
  Same heading logic as add-journal-block. Prefer add-journal-block for most cases —
  it now auto-detects hierarchy.
""")
@click.option("--content", required=True, help="Hierarchical content to add")
@click.option("--date", default=None, help="Date (YYYY-MM-DD), defaults to today")
@click.option("--under-heading", default=None, help="Insert under this heading (e.g. '## Log'). Creates heading if missing. Default from LOGSEQ_JOURNAL_HEADING env var, or top-level if unset.")
@click.option("--top-level", is_flag=True, help="Add as top-level content (ignore --under-heading and env var)")
@click.option("--dry-run", is_flag=True, help="Show what would be written without making changes")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
@handle_connection_error
def add_journal_content(ctx, content, date, under_heading, top_level, dry_run, as_json):
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
    require_content(content)

    if top_level:
        under_heading = None
    else:
        under_heading = resolve_heading(load_config(), under_heading)

    api = ctx.obj["api"]

    if date:
        d = parse_date_keyword(date)
    else:
        d = datetime.date.today()

    configs = api.get_user_configs()
    date_fmt = configs.get("preferredDateFormat") if configs else None
    page_name = format_journal_date(d, date_fmt)

    # Ensure journal page exists with journal property
    try:
        existing = api.get_page(page_name)
    except Exception:
        existing = None
    if not existing and not dry_run:
        api.create_page(page_name, {"journal?": True})

    content = strip_title_heading(content, page_name)
    position = f"under '{under_heading}'" if under_heading else "top-level"

    if dry_run:
        tree = parse_hierarchical_content(content)
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
            tree = parse_hierarchical_content(content)
            uuids = insert_block_tree_with_uuids(api, tree, heading_uuid)
        else:
            click.echo(f"Warning: Could not find or create '{under_heading}', adding as top-level", err=True)
            uuids = insert_formatted_content_with_uuids(api, page_name, content)
            position = "top-level (heading not found)"
    else:
        uuids = insert_formatted_content_with_uuids(api, page_name, content)

    n = len(uuids)
    if as_json:
        output({"page": page_name, "date": str(d), "position": position, "blocks_added": n, "content_added": True, **uuid_fields(uuids)}, True)
    else:
        click.echo(f"Added {n} block(s) to journal: {page_name} ({position})")


# ---------------------------------------------------------------------------
# 16. add-note-content
# ---------------------------------------------------------------------------


# --- Block editing commands ---






# `remove-block` is the canonical name (Logseq's API verb is removeBlock), but
# `delete-page` sits right next to it, so `delete-block` is the single most common
# wrong guess. Register it as an alias so the guess works instead of erroring out.






# ---------------------------------------------------------------------------
# 20b. add-block-ref
# ---------------------------------------------------------------------------






# ---------------------------------------------------------------------------
# 21. get-todos
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 21b. set-todo-status
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 22. get-properties
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 23. set-property
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 24. remove-property
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 25. set-block-property
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 26. rename-page
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 27. delete-page
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 28. query-pages-by-property
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 29. copy-block
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 29b. move-block
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 30. get-page-stats
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 31. doctor
# ---------------------------------------------------------------------------
# Logseq's own rule, from deps/db/src/logseq/db/sqlite/util.cljs:
#     (defn db-based-graph? [graph-name]
#       (when graph-name (string/starts-with? graph-name db-version-prefix)))
# with the two prefixes defined in deps/common/src/logseq/common/config.cljs
# as "logseq_db_" and "logseq_local_". Taken from there rather than inferred
# from an observed response, so the rule rests on the definition both kinds are
# built from.
#
# Not used: logseq.App.checkCurrentIsDbGraph. It is exported in 2.x
# (src/main/logseq/api.cljs) and is the direct answer, but 0.10.15 does not
# carry it — it answers `MethodNotExist: check_current_is_db_graph`, checked
# against the running server. The prefix is the one signal both lines share.
#
# Also not used: logseq.App.getInfo().supportDb. It reads like the flag for
# this, and is not: the implementation returns a hardcoded `true`
# (src/main/logseq/api/app.cljs), meaning "this build can open DB graphs",
# not "this graph is one". On 0.10.15 getInfo does not exist at all.
#
# Rejected as signals, measured against a 1845-page graph: `file` is set on
# 962 pages and `format` on 22, so neither separates the two kinds — they
# only look like they would.






















def main():
    cli()


if __name__ == "__main__":
    main()
