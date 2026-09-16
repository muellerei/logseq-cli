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




def _project_pattern(tag_prefix: str, explicit_tags=None) -> "re.Pattern[str]":
    """Match project mentions, as a tag or as a page link.

    ``#projects/alpha`` and ``[[projects/alpha]]`` name the same project, and a
    graph that namespaces project pages tends to contain both, so matching only
    the tag form undercounts. Group 1 is the project name either way.

    ``explicit_tags`` is for graphs that do not namespace at all: those names
    cannot be inferred from a prefix, so they are listed. They match as a tag
    and as a link for the same reason the namespaced form does — a graph that
    writes ``[[Alpha]]`` in its journals and ``#Alpha`` in passing means the
    same project both times, and counting only one of them undercounts. In the
    journal this was measured against, the flat link outnumbered the flat tag
    by two orders of magnitude, so tags alone would have found nothing.
    """
    prefix = tag_prefix.lstrip("#")
    alts = [
        r"#" + re.escape(prefix) + r"(\S+)",
        r"\[\[" + re.escape(prefix) + r"([^\]]+)\]\]",
    ]
    for raw in explicit_tags or []:
        name = str(raw).strip().lstrip("#")
        if name:
            escaped = re.escape(name)
            alts.append(r"#(" + escaped + r")\b")
            alts.append(r"\[\[(" + escaped + r")\]\]")
    return re.compile("|".join(alts), re.IGNORECASE)


def _word_pattern(words) -> "re.Pattern[str]":
    """Case-insensitive whole-word alternation over a list of words.

    Words come from config, so they are escaped: a user writing "c++" or a
    stray "(" must not turn into a broken or surprising pattern. \\b around a
    word that starts or ends with a non-word character would never match, so
    the boundary is applied per word only where it can bite.
    """
    parts = []
    for raw in words:
        w = str(raw).strip()
        if not w:
            continue
        esc = re.escape(w)
        left = r"\b" if w[0].isalnum() or w[0] == "_" else ""
        right = r"\b" if w[-1].isalnum() or w[-1] == "_" else ""
        parts.append(f"{left}{esc}{right}")
    if not parts:
        # Matches nothing, rather than an empty alternation that matches
        # everywhere and would report every block as a mood hit.
        return re.compile(r"(?!)")
    return re.compile("|".join(parts), re.IGNORECASE)


# A text replacement must skip property lines: rewriting an id:: line breaks
# every ((block-ref)) to that block, irreversibly. Regex shared via helpers.

# find-block --with-children costs one extra read per match (the datalog pull
# carries no children), so the fan-out is capped and the remainder reported.












# ---------------------------------------------------------------------------
# 1. get-all-pages
# ---------------------------------------------------------------------------
@cli.command("get-all-pages", epilog="""\b
Example:
  logseq-cli --token TOKEN get-all-pages --json | head
""")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
@handle_connection_error
def get_all_pages(ctx, as_json):
    """List all pages in the graph."""
    api = ctx.obj["api"]
    pages = api.get_all_pages()

    if as_json:
        output(pages, True)
    else:
        for page in sorted(pages, key=lambda p: (p.get("name") or "").lower()):
            name = page.get("originalName") or page.get("name", "")
            click.echo(name)




def _extract_backlink_context(refs, limit: int) -> list:
    """Extract linking pages together with the blocks that do the linking.

    ``getPageLinkedReferences`` already answers ``[page, [block, ...]]`` pairs,
    so the blocks arrive with the same call that yields the names — no second
    read. ``extract_backlink_names`` keeps only the name; this keeps both.

    ``limit`` caps the blocks kept per page and the remainder is reported as
    ``withheld``, the same bargain the other reads make: a page mentioned fifty
    times must not decide the size of the output.
    """
    if not refs or not isinstance(refs, list):
        return []
    entries = []
    for entry in refs:
        if not (isinstance(entry, (list, tuple)) and len(entry) >= 1):
            continue
        page_info = entry[0]
        if not isinstance(page_info, dict):
            continue
        name = page_info.get("originalName") or page_info.get("name", "")
        if not name:
            continue
        raw_blocks = entry[1] if len(entry) > 1 and isinstance(entry[1], list) else []
        blocks = []
        for block in raw_blocks:
            if not isinstance(block, dict):
                continue
            content = (block.get("content") or "").strip()
            # A properties block is the linking page's own metadata; it holds no
            # mention and would read as context that is not there.
            if not content or is_properties_block(content):
                continue
            blocks.append({"uuid": block.get("uuid", ""), "content": content})
        kept = blocks[:limit] if limit else blocks
        item = {"page": name, "blocks": kept}
        # Counted against what was kept, not against ``limit``: the caller
        # supplies that number, and deriving the count from it is what let a
        # negative value report more withheld than the page ever held.
        if len(kept) < len(blocks):
            item["withheld"] = len(blocks) - len(kept)
        entries.append(item)
    return sorted(entries, key=lambda e: e["page"])










# ---------------------------------------------------------------------------
# 2. get-page
# ---------------------------------------------------------------------------
@cli.command("get-page", epilog="""\b
Examples:
  logseq-cli --token TOKEN get-page --name "Project Alpha"
  logseq-cli --token TOKEN get-page --name "2026-05-08, friday" --resolve-refs --with-ids
  logseq-cli --token TOKEN get-page --name "Project Alpha" --heading "## Open Points"
  logseq-cli --token TOKEN get-page --name A --name B    # batch read
Notes:
  --resolve-refs inlines ((uuid)) block-refs (saves N×get-block).
  --with-ids prefixes each line with the block UUID (replaces --json | jq).
  --heading returns only the matching heading-block + its children.
""")
@click.option("--page", "--name", required=True, multiple=True, help="Page name (repeatable for batch: --name A --name B)")
@click.option("--no-backlinks", is_flag=True, help="Skip backlink computation")
@click.option("--resolve-refs", is_flag=True, help="Inline ((uuid)) block references with their content")
@click.option("--with-ids", "with_ids", is_flag=True, help="Prefix each block line with its UUID (format: <uuid>\\t<indent>\\t<content>)")
@click.option("--heading", default=None, help="Return only the section under this heading (e.g. '## Focus Topics W17'). Searches recursively.")
@click.option("--format", "output_format", type=click.Choice(["text", "markdown"]), default="text", help="Output format: text (default) or markdown (Logseq-compatible)")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
@handle_connection_error
def get_page(ctx, page, no_backlinks, resolve_refs, with_ids, heading, output_format, as_json):
    """Get page content with backlinks. Pass --name multiple times for batch reads."""
    api = ctx.obj["api"]
    missing = []
    dead_refs = []

    def _fetch_one(page_name):
        # A page that does not exist is an error, not an empty result: Logseq's
        # getPage returns null for it but a real object for an existing-but-empty
        # page. Without this check both render as "(empty page)" and the caller
        # cannot tell "typo in the name" from "nothing written yet".
        if api.get_page(page_name) is None:
            missing.append(page_name)
        blocks = api.get_page_blocks_tree(page_name)
        if no_backlinks or heading:
            backlinks = []
        else:
            try:
                refs = api.get_page_linked_references(page_name)
                backlinks = extract_backlink_names(refs)
            except Exception:
                backlinks = find_backlinks(api, page_name)
        if heading and blocks:
            blocks = extract_section(blocks, heading)
            if not blocks:
                click.echo(f"Warning: heading '{heading}' not found in '{page_name}'", err=True)
        if resolve_refs and blocks:
            resolve_refs_in_blocks(api, blocks, dead_refs)
        return {"page": page_name, "blocks": blocks, "backlinks": backlinks}

    results = [_fetch_one(p) for p in page]

    for result in results:
        if result["page"] in missing:
            result["exists"] = False
    if dead_refs:
        for result in results:
            in_this = [u for u in dead_refs
                       if f"(({u}))" in json.dumps(result.get("blocks") or [])]
            if in_this:
                result["dead_refs"] = in_this

    if not resolve_refs:
        total_refs = sum(count_unresolved_refs(r.get("blocks") or []) for r in results)
        if total_refs > 0:
            click.echo(
                f"⚠️  {total_refs} unresolved block-ref(s) in output — "
                f"re-run with --resolve-refs to inline them.",
                err=True,
            )
    elif dead_refs:
        # Only sayable with --resolve-refs: without it nothing is looked up, so
        # a raw ((uuid)) in the output means "not resolved", not "gone". With
        # it, the two look identical on stdout — this is what tells them apart.
        # A notice rather than an error: the page is still readable, and one
        # stale ref must not cost the whole read.
        for uuid in dead_refs:
            results_with = [r["page"] for r in results
                            if f"(({uuid}))" in json.dumps(r.get("blocks") or [])]
            where = f" (on {', '.join(results_with)})" if results_with else ""
            click.echo(f"⚠️  block-ref (({uuid})) points at a block that no "
                       f"longer exists{where}", err=True)

    if as_json:
        output(results if len(results) > 1 else results[0], True)
    else:
        for result in results:
            p, blocks, backlinks = result["page"], result["blocks"], result["backlinks"]
            absent = p in missing
            placeholder = "(page does not exist)" if absent else "(empty page)"
            if with_ids:
                click.echo(f"=== {p} ===\n")
                click.echo(blocks_with_ids(blocks) if blocks else placeholder)
            elif output_format == "markdown":
                click.echo(blocks_to_markdown(blocks) if blocks else placeholder)
            else:
                click.echo(f"=== {p} ===\n")
                click.echo(process_blocks(blocks) if blocks else placeholder)
                if backlinks:
                    click.echo(f"\nBacklinks ({len(backlinks)}):")
                    for bl in backlinks:
                        click.echo(f"  <- {bl}")
            if len(results) > 1:
                click.echo()

    # Exit non-zero if any requested page is absent. Batch reads still print every
    # page that does exist first, so one typo does not cost the whole result.
    # The payload already went to stdout; the error goes to stderr only.
    if missing:
        if as_json:
            click.echo(json.dumps(
                {"error": "Page(s) not found", "missing": missing},
                indent=2, default=str), err=True)
        else:
            for page_name in missing:
                click.echo(f"Error: Page '{page_name}' not found", err=True)
        sys.exit(1)


# ---------------------------------------------------------------------------
# 3. get-block
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 3b. find-block
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 4. search-pages
# ---------------------------------------------------------------------------
@cli.command("search-pages", epilog="""\b
Example:
  logseq-cli --token TOKEN search-pages --query "Roadmap"
Note:
  Case-insensitive substring match on page names. For content search use find-block.
""")
@click.option("--query", required=True, help="Search query (case-insensitive)")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
@handle_connection_error
def search_pages(ctx, query, as_json):
    """Search pages by name (case-insensitive substring match)."""
    api = ctx.obj["api"]
    # Filtered here rather than in datalog, which is deliberate and was
    # measured: pulling all pages costs 187ms of Logseq's own time, the filter
    # below 0.38ms, and the transfer nothing worth naming over loopback.
    # Against that, a query would have to disjoin over :block/original-name and
    # :block/name AND normalise case itself (clojure.string/includes? is
    # case-sensitive) to match what the two lines below do for free - and it
    # would interpolate a user value into datalog, a class of bug this codebase
    # has already paid for once. get_all_pages() is cached and wanted by a
    # dozen other commands anyway. find-block queries datalog because block
    # content is orders of magnitude more data; page names are not.
    pages = api.get_all_pages()
    query_lower = query.lower()
    matches = [
        p for p in pages
        if query_lower in (p.get("name") or "").lower()
        or query_lower in (p.get("originalName") or "").lower()
    ]

    if as_json:
        output(matches, True)
    else:
        if not matches:
            click.echo("No pages found.")
        else:
            click.echo(f"Found {len(matches)} page(s):")
            for p in sorted(matches, key=lambda x: (x.get("name") or "").lower()):
                click.echo(f"  {p.get('originalName') or p.get('name', '')}")


# ---------------------------------------------------------------------------
# 5. get-backlinks
# ---------------------------------------------------------------------------
@cli.command("get-backlinks", epilog="""\b
Examples:
  logseq-cli --token TOKEN get-backlinks --name "Alice"
  logseq-cli --token TOKEN get-backlinks --name "Alice" --name "Bob"    # batch
""")
@click.option("--page", "--name", required=True, multiple=True, help="Page name to find backlinks for (repeatable for batch: --name A --name B)")
@click.option("--with-context", is_flag=True, help="Also show the blocks that do the linking, not just the page names. They come with the same API call, so this costs no extra read")
@click.option("--limit", type=int, default=3, show_default=True, help="With --with-context: blocks kept per linking page; the remainder is reported as withheld. 0 keeps all, negative is rejected")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
@handle_connection_error
def get_backlinks(ctx, page, with_context, limit, as_json):
    """Find pages that link to the given page(s) (uses native Logseq API). Pass --name multiple times for batch."""
    api = ctx.obj["api"]

    if limit < 0:
        fail("--limit must be 0 or greater (0 keeps all).", as_json)

    def _fetch_one(page_name):
        try:
            refs = api.get_page_linked_references(page_name)
            if not refs:
                return []
            if with_context:
                return _extract_backlink_context(refs, limit)
            return extract_backlink_names(refs)
        except (ConnectionError, requests.exceptions.ConnectionError, requests.exceptions.Timeout):
            click.echo("Native backlinks API unavailable, using brute-force scan...", err=True)
            return find_backlinks(api, page_name)
        except Exception as e:
            click.echo(f"Warning: Native backlinks API returned unexpected format ({e}), trying brute-force...", err=True)
            try:
                return find_backlinks(api, page_name)
            except Exception:
                return []

    results = [{"page": p, "backlinks": (bl := _fetch_one(p)), "count": len(bl)} for p in page]

    if as_json:
        output(results if len(results) > 1 else results[0], True)
    else:
        for result in results:
            p, backlinks = result["page"], result["backlinks"]
            if not backlinks:
                click.echo(f"No backlinks found for '{p}'.")
            else:
                click.echo(f"Backlinks to '{p}' ({len(backlinks)}):")
                for bl in backlinks:
                    if isinstance(bl, dict):
                        click.echo(f"  <- {bl['page']}")
                        for block in bl["blocks"]:
                            click.echo(f"       {block['content']}")
                        if bl.get("withheld"):
                            click.echo(f"       ... {bl['withheld']} more not shown")
                    else:
                        click.echo(f"  <- {bl}")
            if len(results) > 1:
                click.echo()


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
@cli.command("analyze-graph", epilog="""\b
Example:
  logseq-cli --token TOKEN analyze-graph --days 30
Note:
  Requires Logseq running — no filesystem fallback possible.
""")
@click.option("--days", default=None, type=int, help="Limit to pages modified in last N days (1 or greater)")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
@handle_connection_error
def analyze_graph(ctx, days, as_json):
    """Analyze the knowledge graph structure."""
    api = ctx.obj["api"]

    # A window, not a cap: a negative value moves the cutoff into the future,
    # so "recently updated" silently empties and the report answers a question
    # nobody asked. 0 puts the cutoff at this moment and is refused for the
    # same reason - it can only ever report pages edited in the future.
    if days is not None and days < 1:
        fail("--days must be 1 or greater.", as_json)

    pages = api.get_all_pages()

    # Open tasks only, and only where Logseq puts a marker: at the start of a
    # block. Matching "todo" anywhere, case-insensitively, counted "Todo-Liste"
    # in prose and the "TODO" inside a DONE block's logbook line, so the number
    # was neither the open tasks nor all of them.
    todo_pattern = re.compile(
        # The bullet may repeat: get_page_content prefixes each block with
        # "- ", so a block that already starts with one arrives as "- - TODO".
        # The checkbox needs its bullet for the same reason the markers need
        # the line anchor: a bare "[ ]" occurs in code snippets, empty
        # markdown links and table cells, none of which are tasks.
        r"(?i:- \[ \])|^(?:\s*-\s*)*(?:TODO|DOING|NOW|LATER|WAITING|IN-PROGRESS)\b",
        re.MULTILINE)
    link_pattern = re.compile(r"\[\[(.*?)\]\]")

    # Days filter: cutoff timestamp in milliseconds
    cutoff_ms = None
    if days is not None:
        cutoff_dt = datetime.datetime.now() - datetime.timedelta(days=days)
        cutoff_ms = int(cutoff_dt.timestamp() * 1000)

    page_names = set()
    journal_count = 0
    total_todos = 0
    reference_count = Counter()
    adjacency = defaultdict(set)
    recently_updated = []

    for page in pages:
        name = page.get("originalName") or page.get("name", "")
        page_names.add(name.lower())
        if page.get("journalDay") or page.get("journal-day") or page.get("journal?"):
            journal_count += 1

    for page in pages:
        name = page.get("originalName") or page.get("name", "")
        updated_at = page.get("updatedAt") or page.get("updated-at") or 0

        # Track recently updated pages when --days is set
        if cutoff_ms and updated_at >= cutoff_ms:
            updated_str = datetime.datetime.fromtimestamp(updated_at / 1000).strftime('%Y-%m-%d %H:%M')
            recently_updated.append({"page": name, "updated": updated_str, "updated_at": updated_at})

        try:
            content = get_page_content(api, name)
        except Exception:
            content = ""

        # count TODOs
        todos = todo_pattern.findall(content)
        total_todos += len(todos)

        # extract links
        links = link_pattern.findall(content)
        for link in links:
            reference_count[link] += 1
            adjacency[name.lower()].add(link.lower())
            adjacency[link.lower()].add(name.lower())

    # Sort recently updated by timestamp descending
    recently_updated.sort(key=lambda x: x["updated_at"], reverse=True)

    # BFS clusters
    visited = set()
    clusters = []

    for node in adjacency:
        if node in visited:
            continue
        cluster = set()
        queue = [node]
        while queue:
            current = queue.pop(0)
            if current in visited:
                continue
            visited.add(current)
            cluster.add(current)
            for neighbor in adjacency.get(current, []):
                if neighbor not in visited:
                    queue.append(neighbor)
        if len(cluster) > 1:
            clusters.append(sorted(cluster))

    top_referenced = reference_count.most_common(15)

    result = {
        "total_pages": len(pages),
        "journal_pages": journal_count,
        "non_journal_pages": len(pages) - journal_count,
        "total_todos": total_todos,
        "top_referenced": [{"page": p, "refs": c} for p, c in top_referenced],
        "clusters": len(clusters),
        "largest_cluster": len(clusters[0]) if clusters else 0,
    }
    if days is not None:
        result["recently_updated"] = [{"page": r["page"], "updated": r["updated"]} for r in recently_updated[:30]]

    if as_json:
        output(result, True)
    else:
        click.echo("=== Graph Analysis ===\n")
        click.echo(f"Total pages:     {result['total_pages']}")
        click.echo(f"Journal pages:   {result['journal_pages']}")
        click.echo(f"Content pages:   {result['non_journal_pages']}")
        click.echo(f"Open TODOs:      {result['total_todos']}")
        click.echo(f"Clusters:        {result['clusters']}")
        if clusters:
            click.echo(f"Largest cluster: {result['largest_cluster']} pages")
        if days is not None and recently_updated:
            click.echo(f"\nRecently Updated (last {days} days): {len(recently_updated)} pages")
            for r in recently_updated[:30]:
                click.echo(f"  {r['page']} ({r['updated']})")
        click.echo(f"\nTop Referenced Pages:")
        for item in result["top_referenced"]:
            click.echo(f"  {item['page']}: {item['refs']} refs")


# ---------------------------------------------------------------------------
# 8. find-knowledge-gaps
# ---------------------------------------------------------------------------
def _is_incidental_page(name: str) -> bool:
    """True for pages that exist as a side effect, not as knowledge.

    Logseq turns `#272` in a sentence into a page called "272", and a stray
    bracket or dash into a page of its own. Those are real pages with no
    incoming links, so they answer "orphaned" truthfully and drown the answer:
    in the graph this was measured against, 596 orphans were almost entirely
    of this kind. Dates written in file-name form are the same story from the
    other side — they look like missing pages but are journals under another
    spelling.
    """
    stripped = name.strip()
    if len(stripped) < 3:
        return True
    if not any(c.isalpha() for c in stripped):
        return True
    if re.fullmatch(r"[\W_]+", stripped):
        return True
    # A name opening with punctuation is a tag that swallowed one: "#-AI"
    if not (stripped[0].isalnum() or stripped[0] in "_@"):
        return True
    # 2025_10_10, 2025-10-10, 2025/10/10 — a journal, not a gap
    if re.fullmatch(r"\d{4}[-_/]\d{1,2}[-_/]\d{1,2}", stripped):
        return True
    # An unclosed bracket dragged in from prose: "#Active)", "3b82f6)".
    if stripped.endswith(")") and "(" not in stripped:
        return True
    # A ticket number that took the next word with it: "#272-Designentscheidung"
    # comes from "#272-Designentscheidung" in a sentence. Three digits or more,
    # so that "2-Faktor-Auth" and "4-Level-Struktur" — real terms — survive.
    if re.match(r"\d{3,}-", stripped):
        return True
    return False


def _has_richer_namesake(name_lower: str, page_names: dict, content_of) -> bool:
    """True if some namespaced page shares this name and actually has content.

    An empty `Alpha` with 185 references sits next to
    `projects/Alpha` with 8806 words: the bare page is an anchor for
    the name, not a gap in the notes. Reporting it as underdeveloped sends the
    reader to write something that is already written next door.
    """
    for other_lower, other_original in page_names.items():
        if other_lower == name_lower:
            continue
        tail = other_lower.rsplit("/", 1)[-1]
        if tail != name_lower:
            continue
        try:
            if len((content_of(other_original) or "").strip()) > 200:
                return True
        except Exception:  # noqa: BLE001 - unreadable page proves nothing
            continue
    return False


@cli.command("find-knowledge-gaps", epilog="""\b
Example:
  logseq-cli --token TOKEN find-knowledge-gaps --min-refs 3 --include-orphans
""")
@click.option("--min-refs", default=2, type=int, help="Min references for underdeveloped detection")
@click.option("--include-orphans/--no-orphans", default=True, help="Include orphaned pages")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
@handle_connection_error
def find_knowledge_gaps(ctx, min_refs, include_orphans, as_json):
    """Find missing, underdeveloped, and orphaned pages."""
    api = ctx.obj["api"]
    pages = api.get_all_pages()

    link_pattern = re.compile(r"\[\[(.*?)\]\]")
    page_names = {}  # lowercase -> original name
    incoming_refs = Counter()
    page_content_lengths = {}

    for page in pages:
        name = page.get("originalName") or page.get("name", "")
        page_names[name.lower()] = name

    # Pass 1: collect all references and content lengths
    for page in pages:
        name = page.get("originalName") or page.get("name", "")
        try:
            content = get_page_content(api, name)
        except Exception:
            content = ""
        page_content_lengths[name.lower()] = len(content)
        links = link_pattern.findall(content)
        for link in links:
            incoming_refs[link.lower()] += 1

    # Missing pages: referenced but don't exist
    missing = []
    for ref_lower, count in incoming_refs.items():
        if ref_lower in page_names:
            continue
        if _is_incidental_page(ref_lower):
            continue
        missing.append({"page": ref_lower, "references": count})
    missing.sort(key=lambda x: x["references"], reverse=True)

    # Underdeveloped: exists, has refs, but very short content
    underdeveloped = []
    for name_lower, original in page_names.items():
        refs = incoming_refs.get(name_lower, 0)
        length = page_content_lengths.get(name_lower, 0)
        if refs >= min_refs and length < 100:
            if _is_incidental_page(original):
                continue
            # An empty page whose namespaced twin is written is an anchor for
            # the name, not a gap. Only checked here, where the list is short.
            if _has_richer_namesake(name_lower, page_names,
                                    lambda n: get_page_content(api, n)):
                continue
            underdeveloped.append({
                "page": original,
                "references": refs,
                "content_length": length,
            })
    underdeveloped.sort(key=lambda x: x["references"], reverse=True)

    # Orphaned: zero incoming references (exclude journals)
    orphans = []
    if include_orphans:
        journal_pages = set()
        for page in pages:
            if page.get("journalDay") or page.get("journal-day") or page.get("journal?"):
                name = page.get("originalName") or page.get("name", "")
                journal_pages.add(name.lower())

        for name_lower, original in page_names.items():
            if name_lower in journal_pages:
                continue
            if _is_incidental_page(original):
                continue
            if incoming_refs.get(name_lower, 0) == 0:
                orphans.append(original)
        orphans.sort(key=str.lower)

    result = {
        "missing_pages": missing[:20],
        "underdeveloped_pages": underdeveloped[:20],
        "orphaned_pages": orphans[:30] if include_orphans else [],
        "summary": {
            "missing": len(missing),
            "underdeveloped": len(underdeveloped),
            "orphaned": len(orphans) if include_orphans else "n/a",
        },
    }

    if as_json:
        output(result, True)
    else:
        click.echo("=== Knowledge Gaps ===\n")
        click.echo(f"Missing pages (referenced but don't exist): {len(missing)}")
        for m in result["missing_pages"]:
            click.echo(f"  {m['page']} ({m['references']} refs)")

        click.echo(f"\nUnderdeveloped pages (< 100 chars, {min_refs}+ refs): {len(underdeveloped)}")
        for u in result["underdeveloped_pages"]:
            click.echo(f"  {u['page']} ({u['references']} refs, {u['content_length']} chars)")

        if include_orphans:
            click.echo(f"\nOrphaned pages (zero incoming links): {len(orphans)}")
            for o in result["orphaned_pages"]:
                click.echo(f"  {o}")


# ---------------------------------------------------------------------------
# 9. analyze-journal-patterns
# ---------------------------------------------------------------------------
@cli.command("analyze-journal-patterns", epilog="""\b
Example:
  logseq-cli --token TOKEN analyze-journal-patterns --timeframe "last 30 days" --mood --topics
""")
@click.option("--timeframe", default="last 30 days", help="Date range for analysis")
@click.option("--mood/--no-mood", default=True, help="Include mood detection")
@click.option("--topics/--no-topics", default=True, help="Include topic analysis")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
@handle_connection_error
def analyze_journal_patterns(ctx, timeframe, mood, topics, as_json):
    """Analyze patterns in journal entries."""
    api = ctx.obj["api"]
    start, end = parse_date_range(timeframe)
    pages = api.get_all_pages()

    # Word lists and the project tag are language- and graph-specific: the
    # built-ins are English, so a journal written in another language scores
    # zero moods and no project progress, silently. [analysis] in the config
    # replaces them; see docs/configuration.md.
    cfg = load_config()
    mood_positive = _word_pattern(
        get(cfg, "analysis", "mood_positive")
        or ["happy", "great", "excited", "good", "wonderful", "productive", "grateful"])
    mood_negative = _word_pattern(
        get(cfg, "analysis", "mood_negative")
        or ["sad", "tired", "stressed", "frustrated", "anxious", "overwhelmed", "bad"])
    mood_labels = get(cfg, "analysis", "mood_labels") or ["mood", "feeling"]
    mood_keyword = re.compile(
        r"(?:" + "|".join(re.escape(str(w)) for w in mood_labels) + r"):\s*(\w+)",
        re.IGNORECASE)
    # Lines worth showing as evidence under the mood counts. Kept to explicit
    # statements and emoji for the same reason the counting is: a bare "happy"
    # in a sentence is as likely to be "not happy", and listing it under a
    # count of zero reads as a contradiction. The labels come from config, so
    # a graph writing "stimmung:" is covered.
    mood_indicator_patterns = [f"{label}:" for label in mood_labels] + [
        "\U0001f60a", "\U0001f614", "\U0001f620", "\U0001f60c",
    ]
    # Two ways of writing a task. Logseq's own are the markers (TODO, DOING,
    # DONE...); the markdown checkbox is what people paste in from elsewhere.
    # Counting only the checkbox reported "0 complete, 0 incomplete" for a
    # graph with over a thousand tasks — a number that reads like a
    # measurement rather than a pattern that cannot match.
    # The markers are upper-case in Logseq and only there, so they are matched
    # case-sensitively: "Now that we finished" and "Later kam die Rückmeldung"
    # open a sentence, not a task. The checkbox alternative keeps (?i), where
    # "[X]" and "[x]" are both in the wild.
    # The bullet may repeat: get_page_content prefixes each block with "- ",
    # so a block already starting with one arrives as "- - TODO ...".
    incomplete_task = re.compile(
        r"(?i:- \[ \])|^(?:\s*-\s*)*(?:TODO|DOING|NOW|LATER|WAITING|IN-PROGRESS)\b",
        re.MULTILINE)
    complete_task = re.compile(
        r"(?i:- \[x\])|^(?:\s*-\s*)*(?:DONE|CANCELED|CANCELLED)\b",
        re.MULTILINE)
    link_pattern = re.compile(r"\[\[(.*?)\]\]")
    # Projects get named in more than one way. A namespace prefix covers both
    # the tag (#projects/alpha) and the link ([[projects/alpha]]), because a
    # graph that namespaces its project pages usually writes both; graphs that
    # tag flatly (#alpha) configure the tags themselves instead.
    project_tag = get(cfg, "analysis", "project_tag_prefix") or "#project/"
    project_pattern = _project_pattern(
        str(project_tag), get(cfg, "analysis", "project_tags"))
    # Habits stay checkbox-only on purpose: a habit is a repeated checkbox
    # list, and treating every TODO as a habit would drown the real ones.
    habit_checkbox = re.compile(r"- \[[ x]\]", re.IGNORECASE)

    entries = []
    topic_by_date = {}
    mood_entries = []
    total_incomplete = 0
    total_complete = 0

    # Extended analysis collectors
    mood_patterns = []  # {date, mood, context}
    habit_patterns = defaultdict(list)  # habit_name -> [{date, done}]
    project_progress = defaultdict(list)  # project -> [{date, status}]
    topics_by_month = defaultdict(set)  # YYYY-MM -> set of topics

    for page in pages:
        jd = page.get("journalDay") or page.get("journal-day")
        if not jd:
            continue
        try:
            d = journal_day_to_date(jd)
        except (ValueError, TypeError):
            continue

        dt = datetime.datetime.combine(d, datetime.time())
        if not (start <= dt <= end):
            continue

        page_name = page.get("originalName") or page.get("name", "")
        try:
            content = get_page_content(api, page_name)
        except Exception:
            content = ""

        date_str = format_journal_date(d)
        month_key = d.strftime("%Y-%m")
        entry = {"date": date_str, "page": page_name}

        # Topics
        if topics:
            links = link_pattern.findall(content)
            entry["topics"] = links
            topic_by_date[date_str] = links
            topics_by_month[month_key].update(links)

        # Mood, from explicit statements only.
        #
        # Counting every occurrence of a positive word measured how often such
        # words appear in technical prose, not how the day went: "nicht
        # zufrieden" and "läuft nicht gut" both scored as positive, and in the
        # journal this was checked against 16% of positive hits were negations
        # — concentrated in exactly the sentences that carry a judgement. A
        # number that says the opposite of its own evidence is worse than no
        # number, and negation is not something a word list can settle.
        #
        # So only a line that states a mood counts: "mood: good",
        # "stimmung: mies" — the labels are configurable, and the word lists
        # now classify that stated value rather than the whole journal.
        if mood:
            mood_data = {"positive": 0, "negative": 0, "keywords": []}
            kw_matches = mood_keyword.findall(content)
            mood_data["keywords"] = kw_matches
            for stated in kw_matches:
                if mood_positive.search(stated):
                    mood_data["positive"] += 1
                elif mood_negative.search(stated):
                    mood_data["negative"] += 1
            entry["mood"] = mood_data
            if mood_data["positive"] or mood_data["negative"] or kw_matches:
                mood_entries.append(entry)

        # Extended mood patterns - per block
        if mood and content:
            for line in content.split("\n"):
                line_stripped = line.strip().lstrip("- ")
                if not line_stripped:
                    continue
                line_lower = line_stripped.lower()
                for indicator in mood_indicator_patterns:
                    if indicator.lower() in line_lower:
                        mood_patterns.append({
                            "date": date_str,
                            "month": month_key,
                            "mood": indicator,
                            "context": line_stripped[:120],
                        })
                        break

        # Habits / tasks
        inc = len(incomplete_task.findall(content))
        comp = len(complete_task.findall(content))
        total_incomplete += inc
        total_complete += comp
        entry["tasks_incomplete"] = inc
        entry["tasks_complete"] = comp

        # Habit tracking - extract individual checkbox items
        for line in content.split("\n"):
            line_stripped = line.strip()
            if habit_checkbox.search(line_stripped):
                done = "[x]" in line_stripped.lower()
                habit_text = re.sub(r"- \[[ x]\]\s*", "", line_stripped, flags=re.IGNORECASE).strip()
                if habit_text:
                    habit_patterns[habit_text].append({"date": date_str, "done": done})

        # Project progress
        for line in content.split("\n"):
            line_stripped = line.strip().lstrip("- ")
            proj_match = project_pattern.search(line_stripped)
            if proj_match:
                # The pattern has one group per spelling (tag, link, and one
                # per configured flat tag), so only one of them is filled.
                name = next((g for g in proj_match.groups() if g), None)
                if name is None:
                    continue
                project_progress[name].append({
                    "date": date_str,
                    "status": line_stripped[:150],
                })

        entries.append(entry)

    # Aggregate topics
    all_topics = Counter()
    for date_topics in topic_by_date.values():
        all_topics.update(date_topics)

    # Compute habit stats
    habit_stats = {}
    for habit_name, occurrences in habit_patterns.items():
        total = len(occurrences)
        done_count = sum(1 for o in occurrences if o["done"])
        # Calculate streaks
        current_streak = 0
        longest_streak = 0
        streak = 0
        for o in occurrences:
            if o["done"]:
                streak += 1
                longest_streak = max(longest_streak, streak)
            else:
                streak = 0
        current_streak = streak
        habit_stats[habit_name] = {
            "total": total,
            "done": done_count,
            "completion_rate": round(done_count / total * 100, 1) if total > 0 else 0,
            "current_streak": current_streak,
            "longest_streak": longest_streak,
        }

    result = {
        "timeframe": timeframe,
        "entries_analyzed": len(entries),
        "tasks": {
            "total_complete": total_complete,
            "total_incomplete": total_incomplete,
            "completion_rate": (
                round(total_complete / (total_complete + total_incomplete) * 100, 1)
                if (total_complete + total_incomplete) > 0
                else 0
            ),
        },
    }
    if topics:
        result["top_topics"] = [{"topic": t, "count": c} for t, c in all_topics.most_common(15)]
    if mood:
        total_pos = sum(e.get("mood", {}).get("positive", 0) for e in entries)
        total_neg = sum(e.get("mood", {}).get("negative", 0) for e in entries)
        result["mood_summary"] = {
            "positive_signals": total_pos,
            "negative_signals": total_neg,
            "mood_keywords": [kw for e in entries for kw in e.get("mood", {}).get("keywords", [])],
        }
        result["mood_patterns"] = mood_patterns
    if habit_stats:
        result["habit_tracking"] = habit_stats
    if project_progress:
        result["project_progress"] = dict(project_progress)
    if topics:
        result["topic_evolution"] = {m: sorted(t) for m, t in sorted(topics_by_month.items(), reverse=True)}
    result["entries"] = entries

    if as_json:
        output(result, True)
    else:
        click.echo(f"=== Journal Patterns ({timeframe}) ===\n")
        click.echo(f"Entries analyzed: {len(entries)}")
        click.echo(f"\nTasks: {total_complete} complete, {total_incomplete} incomplete "
                    f"({result['tasks']['completion_rate']}% rate)")
        if topics and result.get("top_topics"):
            click.echo("\nTop Topics:")
            for t in result["top_topics"][:10]:
                click.echo(f"  {t['topic']}: {t['count']}")
        if mood and result.get("mood_summary"):
            ms = result["mood_summary"]
            click.echo(f"\nMood: +{ms['positive_signals']} positive, -{ms['negative_signals']} negative")
            if ms["mood_keywords"]:
                click.echo(f"  Keywords: {', '.join(ms['mood_keywords'])}")

        # Extended: Mood Patterns
        if mood and mood_patterns:
            click.echo("\nMood Patterns:")
            mood_by_month = defaultdict(list)
            for mp in mood_patterns:
                mood_by_month[mp["month"]].append(mp)
            for month in sorted(mood_by_month.keys(), reverse=True):
                click.echo(f"  {month}:")
                for mp in mood_by_month[month][:5]:
                    click.echo(f"    - {mp['mood']}: \"{mp['context']}\"")

        # Extended: Habit Tracking
        if habit_stats:
            click.echo("\nHabit Tracking:")
            for habit_name, stats in sorted(habit_stats.items(), key=lambda x: x[1]["total"], reverse=True)[:10]:
                click.echo(f"  {habit_name}:")
                click.echo(f"    Completion: {stats['completion_rate']}% ({stats['done']}/{stats['total']})")
                click.echo(f"    Current streak: {stats['current_streak']} days")
                click.echo(f"    Longest streak: {stats['longest_streak']} days")

        # Extended: Project Progress
        if project_progress:
            click.echo("\nProject Progress:")
            for project, updates in sorted(project_progress.items()):
                click.echo(f"  {project}:")
                for u in updates[-5:]:
                    click.echo(f"    - {u['date']}: {u['status']}")

        # Extended: Topic Evolution
        if topics and topics_by_month:
            click.echo("\nTopic Evolution:")
            for month in sorted(topics_by_month.keys(), reverse=True):
                month_topics = sorted(topics_by_month[month])
                click.echo(f"  {month}: {', '.join(month_topics[:15])}")














# ---------------------------------------------------------------------------
# 10. smart-query
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 11. suggest-connections
# ---------------------------------------------------------------------------
@cli.command("suggest-connections", epilog="""\b
Example:
  logseq-cli --token TOKEN suggest-connections --min-confidence 0.7 --max-suggestions 10
""")
@click.option("--min-confidence", default=0.3, type=float, help="Minimum confidence score (0-1)")
@click.option("--min-shared", default=3, type=int, show_default=True,
              help="Minimum shared topics for a pair to count as connected")
@click.option("--max-suggestions", default=10, type=int, help="Maximum suggestions to return (1 or greater); the remainder is reported as withheld")
@click.option("--focus", default=None, help="Focus on specific page/topic")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
@handle_connection_error
def suggest_connections(ctx, min_confidence, min_shared, max_suggestions, focus, as_json):
    """Suggest connections between pages based on shared topics."""
    api = ctx.obj["api"]

    # 0 is refused rather than answered with an empty list: the empty result is
    # reported as "no connections found above confidence threshold", which
    # blames the graph for what the flag did.
    if max_suggestions < 1:
        fail("--max-suggestions must be 1 or greater.", as_json)

    pages = api.get_all_pages()

    # Build topic index: page -> set of topics
    page_topics = {}
    topic_pages = defaultdict(set)

    for page in pages:
        if page.get("journalDay") or page.get("journal-day") or page.get("journal?"):
            continue
        name = page.get("originalName") or page.get("name", "")
        try:
            content = get_page_content(api, name)
        except Exception:
            content = ""

        topics_found = set(extract_topics(content))
        # Also add the page name itself as a topic
        page_topics[name] = topics_found
        for t in topics_found:
            topic_pages[t.lower()].add(name)

    # Calculate similarity between page pairs
    suggestions = []
    page_list = list(page_topics.keys())
    if focus:
        # Only compare focus page against others
        page_list = [p for p in page_list if p.lower() == focus.lower()]

    for i, page_a in enumerate(page_list):
        topics_a = page_topics.get(page_a, set())
        if not topics_a:
            continue

        compare_to = list(page_topics.keys()) if focus else page_list[i+1:]
        for page_b in compare_to:
            if page_a == page_b:
                continue
            topics_b = page_topics.get(page_b, set())
            if not topics_b:
                continue

            # Jaccard similarity on lowercase topics
            a_lower = {t.lower() for t in topics_a}
            b_lower = {t.lower() for t in topics_b}
            intersection = a_lower & b_lower
            union = a_lower | b_lower

            if not union:
                continue

            # Jaccard alone rewards the thinnest evidence there is: two pages
            # that link one page each, the same one, score 1/1 = 1.0 and sort
            # above a pair sharing 35 topics out of 38. A single shared topic
            # is a coincidence, not a connection, so it does not qualify.
            if len(intersection) < min_shared:
                continue

            score = len(intersection) / len(union)
            if score >= min_confidence:
                suggestions.append({
                    "page_a": page_a,
                    "page_b": page_b,
                    "confidence": round(score, 3),
                    "shared_topics": sorted(intersection),
                })

    # Ties on confidence are common and meaningless on their own; the pair with
    # more shared topics is the better suggestion of the two.
    suggestions.sort(key=lambda s: (s["confidence"], len(s["shared_topics"])),
                     reverse=True)
    # Counted before the cap: "found" is a statement about the graph, and
    # counting the survivors would report three pairs as one whenever the cap
    # bites. What the cap left out is named rather than dropped in silence.
    total_found = len(suggestions)
    suggestions = suggestions[:max_suggestions]
    withheld = total_found - len(suggestions)

    result = {
        "suggestions": suggestions,
        "total_found": total_found,
        "min_confidence": min_confidence,
        "min_shared_topics": min_shared,
    }
    if withheld:
        result["withheld"] = withheld

    if as_json:
        output(result, True)
    else:
        if not suggestions:
            click.echo("No connections found above confidence threshold.")
        else:
            click.echo(f"=== Suggested Connections ({len(suggestions)}) ===\n")
            for s in suggestions:
                click.echo(f"  {s['page_a']}  <->  {s['page_b']}")
                click.echo(f"    Confidence: {s['confidence']:.1%}")
                click.echo(f"    Shared: {', '.join(s['shared_topics'][:5])}")
                click.echo()
            # Never truncate silently: the same promise the journal reads make.
            if withheld:
                click.echo(
                    f"Note: showing {len(suggestions)} of {total_found} "
                    f"suggestion(s); {withheld} omitted. Raise --max-suggestions "
                    "to see more.",
                    err=True,
                )


# ---------------------------------------------------------------------------
# 12. create-page
# ---------------------------------------------------------------------------
@cli.command("create-page", epilog="""\b
Example:
  logseq-cli --token TOKEN create-page --name "Alice Example"
Note:
  For pages with properties, use create-page (no --content) + multiple set-property,
  THEN add-note-content for the body. Properties via --content land as bullet-blocks
  (NOT as real properties).
""")
@click.option("--page", "--name", required=True, help="Page name")
@click.option("--content", default=None, help="Initial content for the page")
@click.option("--dry-run", is_flag=True, help="Report whether the page exists and what would be created, without writing")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
@handle_connection_error
def create_page(ctx, page, content, as_json, dry_run):
    """Create a new page, optionally with initial content."""
    api = ctx.obj["api"]

    # Logseq answers createPage for an existing page with that page, so the
    # call alone cannot tell "created" from "was already there" — the command
    # reported success either way, and --content went on to append to the page
    # that existed. A retry after a timeout therefore duplicated content and
    # was told the write had succeeded. Ask first.
    exists = api.get_page(page) is not None

    if dry_run:
        # The preview reports the state the live run would refuse on, rather
        # than refusing here: a preview that exits non-zero is indistinguishable
        # from one that failed to run.
        if as_json:
            output({"page": page, "exists": exists, "would_create": not exists,
                    "has_content": content is not None, "dry_run": True}, True)
        elif exists:
            click.echo(f"[DRY RUN] Page '{page}' already exists — would not be created")
        else:
            click.echo(f"[DRY RUN] Would create page: {page}")
            if content:
                click.echo(f"  content: {content[:60]}{'...' if len(content) > 60 else ''}")
        return

    if exists:
        fail(f"Page '{page}' already exists. Use add-note-content to add to it, "
             "or delete-page first.", as_json=as_json, page=page, exists=True)

    properties = {"journal?": True} if is_journal_date(page) else None
    result = api.create_page(page, properties)

    if content:
        # Unchecked, this appended to a page that create_page may have failed to
        # create, and both failures stayed invisible behind "Created page: ...".
        require_insert(api.append_block_in_page(page, content),
                       f"the initial content on '{page}'")

    if as_json:
        output({"created": page, "page": result, "has_content": content is not None}, True)
    else:
        click.echo(f"Created page: {page}")
        if content:
            click.echo(f"Added content: {content[:60]}{'...' if len(content) > 60 else ''}")


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
@cli.command("add-note-content", epilog="""\b
Examples:
  logseq-cli --token TOKEN add-note-content --page "Alice" --content "Body text"
  logseq-cli --token TOKEN add-note-content --page "Project Alpha" \\
    --under-heading "## Roadmap" --content "Phase 2 - Kickoff"
Note:
  Counterpart of add-journal-block --under-heading for non-journal pages.
  Heading is created if missing.
""")
@click.option("--page", "--name", required=True, help="Page name")
@click.option("--content", required=True, help="Content to add")
@click.option("--create/--no-create", default=True, help="Create page if it doesn't exist")
@click.option("--under-heading", default=None, help="Insert content under this heading; create heading if missing")
@click.option("--property", "properties", multiple=True, help="Set KEY=VALUE property on the created (root) block; repeatable")
@click.option("--dry-run", "dry_run", is_flag=True, help="Show target page, heading and block count, without writing")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
@handle_connection_error
def add_note_content(ctx, page, content, create, under_heading, properties, dry_run, as_json):
    """Add content to any page."""
    api = ctx.obj["api"]

    # Validate property pairs up-front so a bad pair fails before any write.
    try:
        parse_property_pairs(properties)
    except ValueError as e:
        if as_json:
            output({"error": str(e)}, True)
        else:
            click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    # Check if page exists
    existing = None
    try:
        existing = api.get_page(page)
    except Exception:
        pass

    if not existing and not create:
        fail(f"Page '{page}' not found. Use --create to create it.",
             as_json=as_json, page=page, created=False)

    content = strip_title_heading(content, page)

    if dry_run:
        # Everything below this point writes — the page, possibly the heading,
        # then the blocks. The block count comes from the same parse the live
        # path uses, so the preview reports what would actually land, not the
        # raw line count.
        planned = count_blocks(parse_hierarchical_content(content))
        heading_exists = (find_heading(api, page, under_heading) is not None
                          if under_heading and existing else False)
        position = f"under '{under_heading}' on '{page}'" if under_heading else f"'{page}'"
        parsed_properties = dict(parse_property_pairs(properties))

        if as_json:
            output({"page": page, "would_create_page": existing is None,
                    "blocks_added": planned, "under_heading": under_heading,
                    "would_create_heading": bool(under_heading) and not heading_exists,
                    "properties": parsed_properties, "position": position,
                    "dry_run": True}, True)
        else:
            click.echo(f"[DRY RUN] Would add {planned} block(s) to {position}")
            if existing is None:
                click.echo(f"  page: {page} (would be created)")
            if under_heading and not heading_exists:
                click.echo(f"  heading: {under_heading} (would be created)")
            for key, value in parsed_properties.items():
                click.echo(f"  {key}:: {value}")
        return

    if not existing and create:
        api.create_page(page)

    if under_heading:
        heading_uuid = find_or_create_heading(api, page, under_heading)
        if not heading_uuid:
            click.echo(f"Failed to find or create heading '{under_heading}' on '{page}'", err=True)
            sys.exit(1)
        tree = parse_hierarchical_content(content)
        uuids = insert_block_tree_with_uuids(api, tree, heading_uuid)
        position = f"under '{under_heading}' on '{page}'"
    else:
        uuids = insert_formatted_content_with_uuids(api, page, content)
        position = page

    n = len(uuids)
    root_uuid = uuids[0] if uuids else None

    applied = {}
    if properties:
        if root_uuid:
            applied = apply_block_properties(api, root_uuid, properties)
        else:
            click.echo("Warning: no block created, --property ignored", err=True)

    if as_json:
        output({
            "page": page,
            "created": existing is None,
            "blocks_added": n,
            "content_added": True,
            "under_heading": under_heading,
            **uuid_fields(uuids),
            "properties": applied,
        }, True)
    else:
        if existing is None:
            click.echo(f"Created page: {page}")
        click.echo(f"Added {n} block(s) to {position}")
        if root_uuid:
            click.echo(f"  uuid: {root_uuid}")
        for key, value in applied.items():
            click.echo(f"  {key}:: {value}")


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
@cli.command("rename-page", epilog="""\b
Example:
  logseq-cli --token TOKEN rename-page --name "Old Name" --new-name "New Name"
Note:
  Updates all [[Old Name]] references in the graph automatically.
""")
@click.option("--page", "--name", required=True, help="Current page name")
@click.option("--new-name", required=True, help="New page name")
@click.option("--dry-run", "dry_run", is_flag=True, help="Show the rename and the referencing pages, without writing")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
@handle_connection_error
def rename_page(ctx, page, new_name, dry_run, as_json):
    """Rename a page (updates all references across the graph)."""
    api = ctx.obj["api"]

    # Verify page exists first
    page_data = api.get_page(page)
    if not page_data:
        fail(f"Page '{page}' not found", as_json=as_json, page=page)

    if dry_run:
        # A rename reaches past the page itself: Logseq rewrites every [[Old]]
        # in the graph. The blast radius is the point of the preview, so it is
        # worth the extra read here — the write path never needs it. Backlinks
        # are best-effort: if the call fails the rename is still previewed, with
        # the reference count reported as unknown rather than as zero.
        referencing = None
        try:
            refs = api.get_page_linked_references(page)
            referencing = extract_backlink_names(refs) if refs else []
        except Exception as e:
            click.echo(f"Warning: could not read backlinks ({e}); "
                       f"reference count unknown", err=True)

        payload = {"old_name": page, "new_name": new_name, "dry_run": True}
        if referencing is None:
            payload["referencing_pages"] = None
            payload["referencing_page_count"] = None
        else:
            payload["referencing_pages"] = referencing
            payload["referencing_page_count"] = len(referencing)

        if as_json:
            output(payload, True)
        else:
            click.echo(f"[DRY RUN] Would rename page")
            click.echo(f"  from: {page}")
            click.echo(f"  to:   {new_name}")
            if referencing is None:
                click.echo(f"  pages with references that would be rewritten: unknown")
            else:
                click.echo(f"  pages with references that would be rewritten: {len(referencing)}")
                for name in referencing[:10]:
                    click.echo(f"    <- {name}")
                if len(referencing) > 10:
                    click.echo(f"    ... and {len(referencing) - 10} more")
        return

    api.rename_page(page, new_name)

    result = {"old_name": page, "new_name": new_name, "status": "renamed"}
    if as_json:
        output(result, True)
    else:
        click.echo(f"Renamed '{page}' -> '{new_name}'")


# ---------------------------------------------------------------------------
# 27. delete-page
# ---------------------------------------------------------------------------
@cli.command("delete-page", epilog="""\b
Examples:
  logseq-cli --token TOKEN delete-page --name "Obsolete Page" --dry-run
  logseq-cli --token TOKEN delete-page --name "Obsolete Page" --force
Note:
  Destructive. Interactively (TTY) it prompts; non-interactively it REQUIRES
  --force and fails otherwise — --json alone is not a confirmation.
  Backlinks ((uuid)) pointing to deleted blocks become dangling.
""")
@click.option("--page", "--name", required=True, help="Page name to delete")
@click.option("--force", is_flag=True, help="Skip confirmation prompt (required when non-interactive)")
@click.option("--dry-run", is_flag=True, help="Show what would be deleted, without deleting")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
@handle_connection_error
def delete_page(ctx, page, force, dry_run, as_json):
    """Delete a page from the graph."""
    api = ctx.obj["api"]

    # Verify page exists first
    page_data = api.get_page(page)
    if not page_data:
        fail(f"Page '{page}' not found", as_json=as_json, page=page)

    # The block count is what the user decides on, so a failed read must not
    # become a "0". That is the one value that makes a full page look safe to
    # drop, and it feeds the confirmation prompt as well as the preview.
    try:
        blocks = api.get_page_blocks_tree(page) or []
        block_count = count_blocks(blocks)
    except Exception as exc:
        block_count = None
        read_error = exc

    if block_count is None and (dry_run or not force):
        # Both paths exist to let someone decide. Without the count there is
        # nothing to decide on, so they stop instead of showing a number that
        # was never measured. --force is deliberately exempt below: there the
        # count is output, not a gate.
        fail(f"Cannot read the blocks of page '{page}' to report what would be "
             f"deleted ({read_error}). The page was left untouched.",
             as_json=as_json, page=page)

    if dry_run:
        if as_json:
            output({"page": page, "blocks": block_count, "dry_run": True}, True)
        else:
            click.echo(f"[DRY RUN] Would delete page '{page}' ({block_count} block(s))")
        return

    # Confirmation gate. Prompt only when stdin is an interactive terminal;
    # otherwise --force is mandatory. The output format (--json) must never
    # double as a confirmation: a script may request JSON purely to parse data.
    if not force:
        if sys.stdin.isatty():
            if not click.confirm(f"Delete page '{page}' ({block_count} block(s))?"):
                click.echo("Aborted.")
                return
        else:
            fail(
                f"Refusing to delete page '{page}' non-interactively without --force. "
                f"Re-run with --force to confirm, or --dry-run to preview.",
                as_json=as_json, page=page, blocks=block_count,
            )

    api.delete_page(page)

    # Only --force reaches this with an unknown count (see the guard above).
    # "unknown" is the honest word for it: the delete happened, the size did
    # not get measured, and reporting 0 would misdescribe what was removed.
    result = {"page": page, "status": "deleted", "blocks": block_count}
    if as_json:
        output(result, True)
    else:
        size = "unknown" if block_count is None else f"{block_count} block(s)"
        click.echo(f"Deleted page '{page}' ({size})")


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
@cli.command("get-page-stats", epilog="""\b
Example:
  logseq-cli --token TOKEN get-page-stats --name "Alice"
Note:
  Shows blocks, words, inbound/outbound link counts.
""")
@click.option("--page", "--name", required=True, help="Page name")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
@handle_connection_error
def get_page_stats(ctx, page, as_json):
    """Show statistics for a page (blocks, words, links)."""
    api = ctx.obj["api"]
    blocks = api.get_page_blocks_tree(page)
    if blocks is None:
        fail(f"Page '{page}' not found.", as_json=as_json, page=page)

    def _collect(tree):
        total_blocks = 0
        total_words = 0
        outbound = set()
        all_text = []
        for block in tree:
            total_blocks += 1
            content = block.get("content", "")
            all_text.append(content)
            words = len(content.split()) if content.strip() else 0
            total_words += words
            outbound.update(extract_page_links(content))
            children = block.get("children", [])
            if children:
                cb, cw, co, ct = _collect(children)
                total_blocks += cb
                total_words += cw
                outbound.update(co)
                all_text.extend(ct)
        return total_blocks, total_words, outbound, all_text

    block_count, word_count, outbound_links, _ = _collect(blocks)

    # Inbound links via native API
    try:
        refs = api.get_page_linked_references(page)
        inbound = extract_backlink_names(refs)
    except Exception:
        inbound = []

    stats = {
        "page": page,
        "blocks": block_count,
        "words": word_count,
        "outbound_links": sorted(outbound_links),
        "outbound_count": len(outbound_links),
        "inbound_links": inbound,
        "inbound_count": len(inbound),
    }

    if as_json:
        output(stats, True)
    else:
        click.echo(f"=== {page} ===\n")
        click.echo(f"  Blocks:         {block_count}")
        click.echo(f"  Words:          {word_count}")
        click.echo(f"  Outbound links: {len(outbound_links)}")
        click.echo(f"  Inbound links:  {len(inbound)}")
        if outbound_links:
            click.echo(f"\n  Outbound: {', '.join(sorted(outbound_links))}")
        if inbound:
            click.echo(f"\n  Inbound:  {', '.join(inbound)}")


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
