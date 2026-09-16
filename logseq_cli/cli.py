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


@cli.command("update-block", epilog="""\b
Example:
  logseq-cli --token TOKEN update-block --id 12345678-... --content "New text"
  logseq-cli --token TOKEN update-block --where-content "**14:22**" --page "2026-08-21, friday" --content "New text"
Note:
  Use set-property/remove-property for properties, never edit them via update-block.
  Existing block properties survive the update: they are read first and written
  back, so changing the text no longer drops them.
  Use set-todo-status to change TODO/DOING/DONE markers.
  --content is ONE block: newline bullets stay raw text, indented or not.
  Children go in via insert-block --child-of UUID.
  --where-content selects the block by text instead of UUID; it aborts unless
  exactly one block matches, since overwriting the wrong block loses its text.
  Scope it with --page and check with --dry-run.
""")
@click.option("--id", "block_id", default=None, help="UUID of the block to update")
@click.option("--where-content", "where_content", default=None, help="Select the block by content instead of --id; must match exactly one")
@click.option("--page", "--name", "page", default=None, help="With --where-content: restrict the search to this page")
@click.option("--regex", "use_regex", is_flag=True, help="With --where-content: interpret it as a regex")
@click.option("--content", required=True, help="New content for the block")
@click.option("--dry-run", is_flag=True, help="Show the block that would be overwritten, without writing")
@click.option("--json", "as_json", is_flag=True, help="JSON output")
@click.pass_context
@handle_connection_error
def update_block(ctx, block_id, where_content, page, use_regex, content, dry_run, as_json):
    """Update the content of an existing block."""
    # Guard: unlike insert-block / add-journal-block this command has no tree
    # path — it replaces ONE block's content, so newline bullets (indented or
    # flush) would land as raw text inside the block instead of becoming children.
    try:
        reject_unsupported_multiline(content, command="update-block", accepts_tree=False)
    except MultilineContentError as e:
        raise click.UsageError(str(e))

    api = ctx.obj["api"]
    require_content(content)
    if bool(block_id) == bool(where_content):
        fail("Specify exactly one of: --id, --where-content.", as_json=as_json)
    if where_content:
        clean_id = resolve_single_block(api, where_content, page=page, use_regex=use_regex)
    else:
        clean_id = block_id.strip().replace("((", "").replace("))", "")

    # Verify block exists
    block = api.get_block(clean_id, include_children=False)
    if not block:
        fail(f"Block not found: {clean_id}", as_json=as_json, id=clean_id)

    old_content = block.get("content", "") if isinstance(block, dict) else ""
    # Properties are stored inside the block content, so replacing the text
    # would drop them. This command changes text; properties belong to
    # set-block-property / remove-property, and losing them here was a silent
    # side effect nobody asked for. Carrying them through keeps that split
    # honest. ``id::`` is handled by Logseq outside this dict and survives on
    # its own, so block references are unaffected either way.
    kept_properties = block.get("properties") if isinstance(block, dict) else None

    if dry_run:
        if as_json:
            output({"id": clean_id, "old_content": old_content,
                    "new_content": content, "properties": kept_properties or {},
                    "dry_run": True}, True)
        else:
            click.echo(f"[DRY RUN] Would overwrite block {clean_id}")
            if old_content:
                preview = old_content[:60] + ("..." if len(old_content) > 60 else "")
                click.echo(f"  was: {preview}")
            preview = content[:60] + ("..." if len(content) > 60 else "")
            click.echo(f"  now: {preview}")
            if kept_properties:
                click.echo(f"  keeps: {', '.join(f'{k}::' for k in kept_properties)}")
        return

    api.update_block(clean_id, content, properties=kept_properties)

    if as_json:
        output({"id": clean_id, "old_content": old_content, "new_content": content,
                "properties": kept_properties or {}}, True)
    else:
        click.echo(f"Updated block {clean_id}")
        if old_content:
            preview = old_content[:60] + ("..." if len(old_content) > 60 else "")
            click.echo(f"  was: {preview}")
        preview = content[:60] + ("..." if len(content) > 60 else "")
        click.echo(f"  now: {preview}")


@cli.command("remove-block", epilog="""\b
Examples:
  logseq-cli --token TOKEN remove-block --id 12345678-... --dry-run
  logseq-cli --token TOKEN remove-block --id 12345678-...
Note:
  Destructive. Children are removed too — --dry-run reports how many.
  Check get-backlinks first if the block has id::.
""")
@click.option("--id", "block_id", required=True, help="UUID of the block to remove")
@click.option("--dry-run", is_flag=True, help="Show the block and its descendant count, without deleting")
@click.option("--json", "as_json", is_flag=True, help="JSON output")
@click.pass_context
@handle_connection_error
def remove_block_cmd(ctx, block_id, dry_run, as_json):
    """Remove a block by UUID."""
    api = ctx.obj["api"]
    clean_id = block_id.strip().replace("((", "").replace("))", "")

    # Fetch WITH children: removal cascades, so the descendant count is the
    # decisive fact for --dry-run (and for the confirmation the caller may want).
    block = api.get_block(clean_id, include_children=True)
    if not block:
        fail(f"Block not found: {clean_id}", as_json=as_json, id=clean_id)

    content = block.get("content", "") if isinstance(block, dict) else ""
    children = block.get("children", []) if isinstance(block, dict) else []
    descendants = count_blocks(children) if children else 0

    if dry_run:
        if as_json:
            output({"id": clean_id, "content": content,
                    "descendants": descendants, "blocks_removed": descendants + 1,
                    "dry_run": True}, True)
        else:
            click.echo(f"[DRY RUN] Would remove block {clean_id}")
            preview = content[:80] + ("..." if len(content) > 80 else "")
            if preview:
                click.echo(f"  content: {preview}")
            click.echo(f"  descendants that would be removed too: {descendants}")
            click.echo(f"  total blocks affected: {descendants + 1}")
        return

    api.remove_block(clean_id)

    if as_json:
        output({"id": clean_id, "removed": True, "content": content,
                "descendants": descendants, "blocks_removed": descendants + 1}, True)
    else:
        preview = content[:80] + ("..." if len(content) > 80 else "")
        click.echo(f"Removed block {clean_id} ({descendants + 1} block(s) total)")
        if preview:
            click.echo(f"  was: {preview}")


# `remove-block` is the canonical name (Logseq's API verb is removeBlock), but
# `delete-page` sits right next to it, so `delete-block` is the single most common
# wrong guess. Register it as an alias so the guess works instead of erroring out.
cli.add_command(remove_block_cmd, "delete-block")


@cli.command("replace-text", epilog="""\b
Example:
  logseq-cli --token TOKEN replace-text --page "X" --find "old" --replace "new" --dry-run
  logseq-cli --token TOKEN replace-text --page "X" --find "old" --replace "new"
Note:
  ALWAYS run with --dry-run first to preview matches. Prefer set-todo-status
  for TODO->DONE transitions and update-block for block content edits.
""")
@click.option("--page", "--name", required=True, help="Page name to search in")
@click.option("--find", "find_text", required=True, help="Text to find")
@click.option("--replace", "replace_text", required=True, help="Replacement text")
@click.option("--regex", "use_regex", is_flag=True, help="Treat --find as regex pattern")
@click.option("--dry-run", is_flag=True, help="Show matches without replacing")
@click.option("--json", "as_json", is_flag=True, help="JSON output")
@click.pass_context
@handle_connection_error
def replace_text(ctx, page, find_text, replace_text, use_regex, dry_run, as_json):
    """Find and replace text in all blocks of a page."""
    api = ctx.obj["api"]

    blocks = api.get_page_blocks_tree(page)
    if not blocks:
        fail(f"Page '{page}' not found or empty.", as_json=as_json, page=page)

    if use_regex:
        pattern = re.compile(find_text)
    else:
        pattern = re.compile(re.escape(find_text))

    replacements = []

    def scan_blocks(block_list):
        for block in block_list:
            content = block.get("content", "")
            uuid = block.get("uuid", "")
            if not content or not uuid:
                continue
            # Replace only in text lines; a property line (id::/key:: value) is
            # left verbatim so a --find that matches inside it cannot rewrite it.
            new_lines = [
                ln if PROPERTY_LINE_RE.match(ln) else pattern.sub(replace_text, ln)
                for ln in content.split("\n")
            ]
            new_content = "\n".join(new_lines)
            if new_content != content:
                replacements.append({
                    "id": uuid,
                    "old": content,
                    "new": new_content,
                })
                if not dry_run:
                    api.update_block(uuid, new_content)
            children = block.get("children", [])
            if children:
                scan_blocks(children)

    scan_blocks(blocks)

    # updateBlock answers null whether it wrote or not (verified against a live
    # graph), so the write cannot be checked from its return value. Counting the
    # matches instead would report "Replaced N block(s)" for writes that never
    # landed, complete with a before/after diff computed locally. Read the
    # blocks back and compare. See the note above require_insert() in helpers.py
    # for when this read can be dropped.
    failed = []
    if replacements and not dry_run:
        for r in replacements:
            after = api.get_block(r["id"], include_children=False) or {}
            if after.get("content") != r["new"]:
                failed.append(r["id"])

    if as_json:
        payload = {"page": page, "replacements": len(replacements) - len(failed),
                   "dry_run": dry_run, "matches": replacements}
        if failed:
            payload["failed"] = failed
        output(payload, True)
    else:
        if not replacements:
            click.echo(f"No matches for '{find_text}' in '{page}'.")
        else:
            action = "Would replace" if dry_run else "Replaced"
            click.echo(f"{action} {len(replacements) - len(failed)} block(s) in '{page}':")
            for r in replacements:
                old_preview = r["old"][:60] + ("..." if len(r["old"]) > 60 else "")
                new_preview = r["new"][:60] + ("..." if len(r["new"]) > 60 else "")
                mark = "  !! not written" if r["id"] in failed else ""
                click.echo(f"  {r['id'][:8]}..  {old_preview}{mark}")
                click.echo(f"         →  {new_preview}")
            if failed:
                fail(f"{len(failed)} of {len(replacements)} replacement(s) did not "
                     "reach the graph. Logseq reports no error for this, so the "
                     "blocks were read back to check.", as_json=as_json,
                     failed=failed)


@cli.command("insert-block", epilog="""\b
Examples:
  logseq-cli --token TOKEN insert-block --child-of UUID --content "Sub-Block"
  logseq-cli --token TOKEN insert-block --after UUID --content "Sibling block"
  logseq-cli --token TOKEN insert-block --child-of UUID --first --content "New first child"
  logseq-cli --token TOKEN insert-block --child-of UUID \\
    --tree "Parent\\n\\tChild1\\n\\tChild2\\n\\t\\tGrandchild"
  logseq-cli --token TOKEN insert-block --page "X" --top-level \\
    --tree '[{"content":"...","children":[{"content":"..."}]}]'
Notes:
  --tree accepts tab-indented text OR JSON (auto-detected). Use it instead of
  N×insert-block for hierarchies — single API roundtrip.
  --content, --tree and --tree-file are mutually exclusive.
  --tree-file reads the same tab-indented text (or JSON) from a file, so
  apostrophes/quotes/umlauts need no shell quoting.
  --child-of UUID also accepts hierarchical --content (same tab-indent format).
  --first puts the block at the HEAD of the child list instead of appending it
  last; it only applies together with --child-of.
  id:: lines in a tree are dropped unless --keep-ids is given, and the command
  says so. Use --keep-ids when moving or restoring an outline; do NOT use it
  when copying one whose original still exists, or two blocks share a uuid.
""")
@click.option("--page", "--name", default=None, help="Page name (append to end of page)")
@click.option("--after", default=None, help="UUID of block to insert after (as sibling)")
@click.option("--before", default=None, help="UUID of block to insert before (as sibling)")
@click.option("--child-of", default=None, help="UUID of parent block (insert as child)")
@click.option("--first", "as_first", is_flag=True, help="With --child-of: insert as FIRST child instead of appending last")
@click.option("--top-level", is_flag=True, help="With --page and --tree: insert at page top-level")
@click.option("--content", default=None, help="Content for the new block")
@click.option("--tree", "tree_input", default=None, help="Tab-indented hierarchy or JSON array of {content, children} nodes")
@click.option("--tree-file", "tree_file", default=None, help="Read the tree (tab-indented text or JSON) from a file. Mutually exclusive with --tree and --content.")
@click.option("--property", "properties", multiple=True, help="Set KEY=VALUE property on the created (root) block; repeatable")
@click.option("--keep-ids", "keep_ids", is_flag=True, help="Keep the id:: values in the tree instead of letting Logseq mint new ones. For moving or restoring an outline; do NOT use when copying one that still exists, as two blocks would share a uuid")
@click.option("--dry-run", is_flag=True, help="Show what would be inserted (block count + position) without writing")
@click.option("--quiet", is_flag=True, help="With --tree: print only the confirmation line, not one uuid line per block")
@click.option("--json", "as_json", is_flag=True, help="JSON output")
@click.pass_context
@handle_connection_error
def insert_block_cmd(ctx, page, after, before, child_of, as_first, top_level, content, tree_input, tree_file, properties, keep_ids, dry_run, quiet, as_json):
    """Insert a block (or tree of blocks) at a specific position."""
    api = ctx.obj["api"]

    # --tree-file is --tree from a file; resolve it before any other validation
    # so the rest of the command sees a single tree_input.
    if tree_file is not None:
        if tree_input is not None:
            click.echo("Specify either --tree or --tree-file, not both.", err=True)
            sys.exit(1)
        tree_input = read_content_file(tree_file)

    # Validate property pairs up-front so a bad pair fails before any write.
    try:
        parse_property_pairs(properties)
    except ValueError as e:
        if as_json:
            output({"error": str(e)}, True)
        else:
            click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if tree_input is not None:
        if content is not None:
            click.echo("Specify either --content or --tree, not both.", err=True)
            sys.exit(1)
        tree = parse_tree_input(tree_input)
        if not tree:
            click.echo("Tree input is empty.", err=True)
            sys.exit(1)

        # An id:: in the tree names a UUID the block is meant to keep. Logseq
        # only honours it when the write asks for it, so without --keep-ids
        # those ids are dropped and every ((uuid)) pointing at them dangles.
        # That used to happen silently; it is now either refused or announced.
        tree_ids = collect_block_ids(tree)
        if keep_ids:
            bad = invalid_block_ids(tree)
            if bad:
                msg = (f"{len(bad)} id:: value(s) are not valid UUIDs and cannot become "
                       f"block ids: {', '.join(bad[:3])}"
                       f"{' ...' if len(bad) > 3 else ''}. Nothing was written.")
                if as_json:
                    output({"error": msg, "invalid_ids": bad}, True)
                else:
                    click.echo(f"Error: {msg}", err=True)
                sys.exit(1)
        elif tree_ids:
            click.echo(
                f"Note: {len(tree_ids)} id:: propert(ies) in the tree will be dropped; "
                "Logseq mints new UUIDs and any ((uuid)) pointing at the old ones "
                "will dangle. Pass --keep-ids to preserve them (only when the "
                "source outline is gone, or two blocks would share a uuid).",
                err=True,
            )

        # Resolve target + position first (no writes), so --dry-run can report
        # the plan and bail before touching the graph.
        if child_of:
            clean_id = child_of.strip().replace("((", "").replace("))", "")
            position = f"{'first child' if as_first else 'child'} of {clean_id[:8]}..."
            if as_first:
                do_insert = lambda: insert_block_tree_as_first_children(api, tree, clean_id, keep_ids=keep_ids)
            else:
                do_insert = lambda: insert_block_tree_with_uuids(api, tree, clean_id, strict=True, keep_ids=keep_ids)
        elif after:
            clean_id = after.strip().replace("((", "").replace("))", "")
            position = f"after {clean_id[:8]}..."
            do_insert = lambda: insert_block_tree_as_siblings(api, tree, clean_id, before=False, keep_ids=keep_ids)
        elif before:
            clean_id = before.strip().replace("((", "").replace("))", "")
            position = f"before {clean_id[:8]}..."
            do_insert = lambda: insert_block_tree_as_siblings(api, tree, clean_id, before=True, keep_ids=keep_ids)
        elif page and top_level:
            position = f"top-level of '{page}'"
            if keep_ids and any(block_id_property(b.get("content", "")) for b in tree):
                # appendBlockInPage takes no options, so the roots cannot keep
                # their ids here. Saying so beats a flag that half works.
                click.echo(
                    "Note: --keep-ids cannot preserve ids on top-level blocks "
                    "(the page-append API takes no uuid); their children keep theirs. "
                    "Insert relative to a block (--child-of/--after/--before) to keep all of them.",
                    err=True,
                )
            do_insert = lambda: insert_block_tree_at_page_top(api, tree, page, keep_ids=keep_ids)
        else:
            click.echo(
                "Tree insert requires --child-of, --after, --before, or --page NAME --top-level",
                err=True,
            )
            sys.exit(1)

        if dry_run:
            planned = count_blocks(tree)
            if as_json:
                output({"position": position, "blocks": planned, "dry_run": True}, True)
            else:
                click.echo(f"[DRY RUN] Would insert {planned} block(s) {position}")
            return

        uuids = do_insert()
        root_uuid = uuids[0] if uuids else None
        applied = {}
        if properties:
            if root_uuid:
                applied = apply_block_properties(api, root_uuid, properties)
            else:
                click.echo("Warning: no block created, --property ignored", err=True)

        if as_json:
            output({
                "position": position,
                **uuid_fields(uuids),
                "blocks_added": len(uuids),
                "properties": applied,
            }, True)
        else:
            click.echo(f"Inserted {len(uuids)} block(s) {position}")
            if not quiet:
                # One line per block: useful when a UUID is needed downstream,
                # noise when only the confirmation matters, which is why this is
                # suppressible rather than always printed.
                for u in uuids:
                    click.echo(f"  uuid: {u}")
            for key, value in applied.items():
                click.echo(f"  {key}:: {value}")
        return

    if content is None:
        click.echo("Specify --content or --tree.", err=True)
        sys.exit(1)
    require_content(content)

    targets = sum(1 for x in [page, after, before, child_of] if x)
    if targets == 0:
        click.echo("Specify one of: --page, --after, --before, --child-of", err=True)
        sys.exit(1)
    if targets > 1:
        click.echo("Specify only one of: --page, --after, --before, --child-of", err=True)
        sys.exit(1)
    if as_first and not child_of:
        click.echo("--first only applies to --child-of (it selects the first child position).", err=True)
        sys.exit(1)

    result = None
    position = ""
    new_uuid = None
    hierarchical = contains_hierarchical_content(content)

    if dry_run:
        planned = count_blocks(parse_hierarchical_content(content)) if hierarchical else 1
        target = page or (f"after {after[:8]}..." if after else
                          f"before {before[:8]}..." if before else
                          f"{'first child' if as_first else 'child'} of {child_of[:8]}...")
        target_desc = f"end of '{page}'" if page else target
        if as_json:
            output({"position": target_desc, "blocks": planned, "dry_run": True}, True)
        else:
            click.echo(f"[DRY RUN] Would insert {planned} block(s) {target_desc}")
        return

    if page:
        if hierarchical:
            tree = parse_hierarchical_content(content)
            uuids = insert_formatted_content_with_uuids(api, page, content)
            new_uuid = uuids[0] if uuids else None
            result = {"blocks_added": len(uuids), "uuids": uuids}
            position = f"end of '{page}' ({len(uuids)} block(s))"
        else:
            result = api.append_block_in_page(page, content)
            new_uuid = require_insert(result, f"a block in '{page}'")
            position = f"end of '{page}'"
    elif after:
        clean_id = after.strip().replace("((", "").replace("))", "")
        if hierarchical:
            tree = parse_hierarchical_content(content)
            uuids = insert_block_tree_as_siblings(api, tree, clean_id, before=False)
            new_uuid = uuids[0] if uuids else None
            result = {"blocks_added": len(uuids), "uuids": uuids}
            position = f"after {clean_id[:8]}... ({len(uuids)} block(s))"
        else:
            result = api.insert_block(clean_id, content, {"sibling": True, "before": False})
            new_uuid = require_insert(result, f"a block after {clean_id[:8]}...")
            position = f"after {clean_id[:8]}..."
    elif before:
        clean_id = before.strip().replace("((", "").replace("))", "")
        if hierarchical:
            tree = parse_hierarchical_content(content)
            uuids = insert_block_tree_as_siblings(api, tree, clean_id, before=True)
            new_uuid = uuids[0] if uuids else None
            result = {"blocks_added": len(uuids), "uuids": uuids}
            position = f"before {clean_id[:8]}... ({len(uuids)} block(s))"
        else:
            result = api.insert_block(clean_id, content, {"sibling": True, "before": True})
            new_uuid = require_insert(result, f"a block before {clean_id[:8]}...")
            position = f"before {clean_id[:8]}..."
    elif child_of:
        clean_id = child_of.strip().replace("((", "").replace("))", "")
        where = "first child" if as_first else "child"
        if hierarchical:
            tree = parse_hierarchical_content(content)
            if as_first:
                uuids = insert_block_tree_as_first_children(api, tree, clean_id)
            else:
                uuids = insert_block_tree_with_uuids(api, tree, clean_id, strict=True)
            new_uuid = uuids[0] if uuids else None
            result = {"blocks_added": len(uuids), "uuids": uuids}
            position = f"{where} of {clean_id[:8]}... ({len(uuids)} block(s))"
        else:
            opts = {"sibling": False, "before": True} if as_first else {"sibling": False}
            result = api.insert_block(clean_id, content, opts)
            new_uuid = require_insert(result, f"a {where} of {clean_id[:8]}...")
            position = f"{where} of {clean_id[:8]}..."

    if new_uuid is None and isinstance(result, dict):
        new_uuid = result.get("uuid")

    applied = {}
    if properties:
        if new_uuid:
            applied = apply_block_properties(api, new_uuid, properties)
        else:
            click.echo("Warning: no block uuid returned, --property ignored", err=True)

    if as_json:
        output({"position": position, "content": content, "result": result, "properties": applied, **uuid_fields([u for u in [new_uuid] if u])}, True)
    else:
        click.echo(f"Inserted block {position}")
        preview = content[:80] + ("..." if len(content) > 80 else "")
        click.echo(f"  {preview}")
        if new_uuid:
            click.echo(f"  uuid: {new_uuid}")
        for key, value in applied.items():
            click.echo(f"  {key}:: {value}")


# ---------------------------------------------------------------------------
# 20b. add-block-ref
# ---------------------------------------------------------------------------
@cli.command("add-block-ref", epilog="""\b
Examples:
  logseq-cli --token TOKEN add-block-ref --source-id UUID --under-heading "## Tasks"
  logseq-cli --token TOKEN add-block-ref --source-id UUID --journal-date 2026-04-23 \\
                                          --under-heading "## Tasks"
  logseq-cli --token TOKEN add-block-ref --source-id UUID --page "Project Alpha" \\
                                          --under-heading "## Open TODOs"
Note:
  Default target: today's journal. Auto-creates the journal page if missing.
""")
@click.option("--source-id", required=True, help="UUID of the block to reference")
@click.option("--journal-date", default=None, help="Target journal date (YYYY-MM-DD), defaults to today")
@click.option("--page", "--name", default=None, help="Target page name (alternative to --journal-date)")
@click.option("--under-heading", default=None, help="Insert under this heading. Defaults to LOGSEQ_JOURNAL_HEADING env var, or top-level.")
@click.option("--dry-run", "dry_run", is_flag=True, help="Show source, target page and heading, without writing")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
@handle_connection_error
def add_block_ref(ctx, source_id, journal_date, page, under_heading, dry_run, as_json):
    """Insert a ((block-reference)) to a target journal or page.

    Useful for carrying over TODOs from project pages into a journal's ## Tasks section.

    Examples:
      logseq-cli add-block-ref --source-id UUID --under-heading "## Tasks"
      logseq-cli add-block-ref --source-id UUID --journal-date 2026-04-23 --under-heading "## Tasks"
    """
    api = ctx.obj["api"]

    if not journal_date and not page:
        # Default: today's journal
        import datetime as _dt
        journal_date = _dt.date.today().strftime("%Y-%m-%d")

    would_create_page = False
    if journal_date and not page:
        d = parse_date_keyword(journal_date)
        configs = api.get_user_configs()
        date_fmt = configs.get("preferredDateFormat") if configs else None
        page = format_journal_date(d, date_fmt)
        # Ensure journal page exists
        try:
            existing = api.get_page(page)
        except Exception:
            existing = None
        if not existing:
            would_create_page = True
            # Creating the journal page is itself a write, so under --dry-run it
            # is only reported, never done.
            if not dry_run:
                api.create_page(page, {"journal?": True})

    source_id = source_id.strip("()")
    ref_content = f"(({source_id}))"

    under_heading = resolve_heading(load_config(), under_heading)

    if dry_run:
        # A block-ref is only worth anything if its source exists; a typo'd UUID
        # writes a ((...)) that renders as nothing. The live path cannot check
        # this without an extra call, but the preview can afford one.
        source_block = api.get_block(source_id, include_children=False)
        source_content = (source_block.get("content", "")
                          if isinstance(source_block, dict) else "")
        # Look the heading up WITHOUT creating it — find_or_create_heading would
        # append it to the page and make the preview a write.
        heading_exists = (find_heading(api, page, under_heading) is not None
                          if under_heading and not would_create_page else False)
        if under_heading:
            position = f"under '{under_heading}' on '{page}'"
        else:
            position = f"top-level on '{page}'"

        if as_json:
            output({"source_id": source_id, "ref": ref_content, "page": page,
                    "position": position, "under_heading": under_heading,
                    "source_exists": bool(source_block),
                    "source_content": source_content,
                    "would_create_page": would_create_page,
                    "would_create_heading": bool(under_heading) and not heading_exists,
                    "dry_run": True}, True)
        else:
            click.echo(f"[DRY RUN] Would add block-ref {position}")
            click.echo(f"  ref: {ref_content}")
            if source_block:
                preview = source_content[:60] + ("..." if len(source_content) > 60 else "")
                click.echo(f"  source: {preview}")
            else:
                click.echo(f"  source: WARNING - block {source_id} not found; "
                           f"the ref would render as nothing")
            click.echo(f"  target page: {page}"
                       f"{' (would be created)' if would_create_page else ''}")
            if under_heading:
                click.echo(f"  heading: {under_heading}"
                           f"{'' if heading_exists else ' (would be created)'}")
        return

    if under_heading:
        heading_uuid = find_or_create_heading(api, page, under_heading)
        if heading_uuid:
            result = api.insert_block(heading_uuid, ref_content, {"sibling": False})
            position = f"under '{under_heading}' on '{page}'"
        else:
            result = api.append_block_in_page(page, ref_content)
            position = f"top-level on '{page}' (heading not found)"
    else:
        result = api.append_block_in_page(page, ref_content)
        position = f"top-level on '{page}'"

    # A ref that was never written is worse than a visible error: the TODO looks
    # linked on the project page and silently is not, which is exactly what
    # block-refs are relied on for.
    new_uuid = require_insert(result, f"the block-ref {position}")

    if as_json:
        output({"source_id": source_id, "ref": ref_content, "page": page, "position": position, "uuid": new_uuid}, True)
    else:
        click.echo(f"Added block-ref {position}")
        click.echo(f"  {ref_content}")
        click.echo(f"  uuid: {new_uuid}")






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
@cli.command("copy-block", epilog="""\b
Examples:
  logseq-cli --token TOKEN copy-block --id UUID --to-page "Target Page"
  logseq-cli --token TOKEN copy-block --id UUID --to-page "Target Page" --remove
Note:
  Copies block + all children. With --remove: original is deleted (move).
""")
@click.option("--id", "block_id", required=True, help="Source block UUID")
@click.option("--to-page", required=True, help="Target page name")
@click.option("--remove", is_flag=True, help="Remove source block after copying (move)")
@click.option("--dry-run", is_flag=True, help="Show what would be copied/moved, without writing")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
@handle_connection_error
def copy_block(ctx, block_id, to_page, remove, dry_run, as_json):
    """Copy a block (with children) to another page."""
    api = ctx.obj["api"]
    block_id = block_id.strip("()")
    source = api.get_block(block_id, include_children=True)
    if not source:
        fail("Block not found.", as_json=as_json, id=block_id)

    if dry_run:
        planned = count_blocks([source])
        action = "move" if remove else "copy"
        content = source.get("content", "") if isinstance(source, dict) else ""
        if as_json:
            output({"action": action, "blocks": planned, "to_page": to_page,
                    "source_id": block_id, "removes_source": bool(remove),
                    "dry_run": True}, True)
        else:
            click.echo(f"[DRY RUN] Would {action} {planned} block(s) to '{to_page}'")
            preview = content[:80] + ("..." if len(content) > 80 else "")
            if preview:
                click.echo(f"  root: {preview}")
            if remove:
                click.echo(f"  source block {block_id} WOULD BE REMOVED after copying")
        return

    # Every insert is checked: Logseq answers a failed write with HTTP 200 +
    # null, so an unchecked copy reports "Moved N block(s)" with exit 0 while
    # nothing arrived. With --remove that unverified success would then delete
    # the source, which destroys the block for good.
    written = [0]

    def _copy_tree(block, parent_uuid=None):
        content = block.get("content", "")
        if parent_uuid:
            result = api.insert_block(parent_uuid, content, {"sibling": False})
            new_uuid = require_insert(
                result, "a copied block", written_so_far=written[0])
        else:
            result = api.append_block_in_page(to_page, content)
            new_uuid = require_insert(
                result, f"the copied block on '{to_page}'", written_so_far=written[0])
        written[0] += 1
        copied = 1
        for child in block.get("children", []):
            copied += _copy_tree(child, new_uuid)
        return copied

    count = _copy_tree(source)

    if remove:
        # Only reached when every insert above returned a UUID, so the source is
        # removed against a copy that is known to exist, never a claimed one.
        api.remove_block(block_id)

    action = "Moved" if remove else "Copied"
    result_data = {"action": action.lower(), "blocks": count, "to_page": to_page, "source_id": block_id}

    if as_json:
        output(result_data, True)
    else:
        click.echo(f"{action} {count} block(s) to '{to_page}'.")


# ---------------------------------------------------------------------------
# 29b. move-block
# ---------------------------------------------------------------------------
@cli.command("move-block", epilog="""\b
Examples:
  logseq-cli --token TOKEN move-block --id UUID --under UUID
  logseq-cli --token TOKEN move-block --id UUID --before UUID
Note:
  Structural move: the block keeps its UUID, so ((block-refs)) to it survive.
  Prefer this over `copy-block --remove`, which writes a new block (new UUID,
  dead refs) and deletes the original.
  --under nests the block as the target's FIRST child; --before puts it directly
  in front of the target as a sibling. Children always move along.
  A block cannot be moved into its own subtree; Logseq refuses that silently, so
  the move is verified by re-reading and reported as an error if it did not take.
""")
@click.option("--id", "block_id", required=True, help="UUID of the block to move")
@click.option("--under", default=None, help="UUID of the new parent (block becomes its first child)")
@click.option("--before", default=None, help="UUID of the block to move in front of (as sibling)")
@click.option("--dry-run", is_flag=True, help="Show what would be moved, without writing")
@click.option("--json", "as_json", is_flag=True, help="JSON output")
@click.pass_context
@handle_connection_error
def move_block_cmd(ctx, block_id, under, before, dry_run, as_json):
    """Move a block (with children) under or before another block."""
    api = ctx.obj["api"]
    if bool(under) == bool(before):
        fail("Specify exactly one of: --under, --before.", as_json=as_json)

    target = under or before
    block_id = block_id.strip("()")
    source = api.get_block(block_id, include_children=True)
    if not source:
        fail("Block not found.", as_json=as_json, id=block_id)

    position = f"under {target[:8]}..." if under else f"before {target[:8]}..."
    if dry_run:
        planned = count_blocks([source])
        content = source.get("content", "") if isinstance(source, dict) else ""
        if as_json:
            output({"action": "move", "blocks": planned, "position": position,
                    "source_id": block_id, "dry_run": True}, True)
        else:
            click.echo(f"[DRY RUN] Would move {planned} block(s) {position}")
            preview = content[:80] + ("..." if len(content) > 80 else "")
            if preview:
                click.echo(f"  root: {preview}")
        return

    move_block_verified(api, block_id, target, before=bool(before))
    count = count_blocks([source])

    if as_json:
        output({"action": "move", "blocks": count, "position": position,
                "source_id": block_id}, True)
    else:
        click.echo(f"Moved {count} block(s) {position}")


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
