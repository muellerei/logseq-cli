import json
import os
import re
import sys
import datetime
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from importlib.metadata import version as _pkg_version, PackageNotFoundError
from pathlib import Path

import click
import requests

from logseq_cli.api import LogseqAPI
from logseq_cli.helpers import (
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
    contains_hierarchical_content,
    has_flush_newline_bullets,
    has_mixed_indentation,
    normalize_indentation,
    insert_formatted_content,
    find_or_create_heading,
    insert_block_tree,
    insert_block_tree_with_uuids,
    insert_block_tree_as_siblings,
    insert_block_tree_at_page_top,
    insert_formatted_content_with_uuids,
    block_uuid_from_result,
    require_insert,
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


def handle_connection_error(func):
    """Decorator to catch connection errors and print a helpful message."""
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except requests.ConnectionError:
            click.echo(
                "Error: Cannot connect to Logseq API. "
                "Is Logseq running with the HTTP API enabled?",
                err=True,
            )
            sys.exit(1)
        except requests.HTTPError as e:
            click.echo(f"Error: HTTP {e.response.status_code} - {e.response.text}", err=True)
            sys.exit(1)
    wrapper.__name__ = func.__name__
    wrapper.__doc__ = func.__doc__
    return wrapper


def output(data, as_json: bool, human_formatter=None):
    """Output data as JSON or human-readable text."""
    if as_json:
        click.echo(json.dumps(data, indent=2, default=str))
    elif human_formatter:
        click.echo(human_formatter(data))
    else:
        click.echo(data)


def fail(message: str, as_json: bool = False, exit_code: int = 1, **fields):
    """Report an error and exit with ``exit_code`` (never returns).

    Errors always go to **stderr**, never stdout — stdout stays reserved for
    payload, so a caller parsing stdout as JSON is never handed an error object
    where data was expected. With ``--json`` the error is emitted as a JSON
    object (``{"error": ..., ...fields}``) so agents can parse it structurally
    instead of scraping prose; without it, a plain ``Error: ...`` line.

    ``fields`` adds context keys (e.g. ``id=...``, ``page=...``) to the JSON form.
    """
    if as_json:
        payload = {"error": message, **fields}
        click.echo(json.dumps(payload, indent=2, default=str), err=True)
    else:
        click.echo(f"Error: {message}", err=True)
    sys.exit(exit_code)


_BLOCK_REF_RE = re.compile(r'\(\(([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\)\)')
_TODO_MARKERS = {"TODO", "DOING", "DONE", "LATER", "NOW", "CANCELED", "WAIT", "WAITING"}


def _resolve_single_ref(api, uuid: str) -> str:
    """Resolve one block UUID to its content text. Returns UUID unchanged on failure."""
    try:
        block = api.get_block(uuid, include_children=False)
        if block:
            ref_content = (block.get("content") or "").strip()
            page_info = block.get("page") or {}
            page_name = ""
            if isinstance(page_info, dict):
                page_name = page_info.get("originalName") or page_info.get("name") or ""
            source = f" ↳ {page_name}" if page_name else ""
            return f"{ref_content}{source}"
    except Exception:
        pass
    return f"(({uuid}))"


def _resolve_refs_in_blocks(api, blocks: list) -> None:
    """Recursively resolve ((uuid)) references in block content, in-place."""
    for block in blocks:
        content = block.get("content", "")
        if content and "((" in content:
            block["content"] = _BLOCK_REF_RE.sub(
                lambda m: _resolve_single_ref(api, m.group(1)), content
            )
        children = block.get("children", [])
        if children:
            _resolve_refs_in_blocks(api, children)


def _swap_todo_marker(content: str, new_status: str) -> str:
    """Replace the leading TODO-marker in content with new_status."""
    parts = content.split(None, 1)
    if parts and parts[0].upper() in _TODO_MARKERS:
        rest = parts[1] if len(parts) > 1 else ""
        return f"{new_status} {rest}".strip()
    return f"{new_status} {content}"


def _resolve_version() -> str:
    """Single source of truth for the CLI version.

    Reads pyproject.toml when running from a source checkout (the authoritative
    value during development), else falls back to the installed package metadata.
    Avoids the stale hardcoded-version drift that previously made --version lie.
    """
    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    try:
        for line in pyproject.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("version"):
                # version = "0.5.0"
                return stripped.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    try:
        return _pkg_version("logseq-cli")
    except PackageNotFoundError:
        return "unknown"


@click.group()
@click.version_option(version=_resolve_version(), prog_name="logseq-cli")
@click.option("--host", default=None, help="Logseq API host (default: 127.0.0.1)")
@click.option("--port", default=None, help="Logseq API port (default: 12315)")
@click.option("--token", default=None, help="Logseq API Bearer token")
@click.option("--no-cache", "no_cache", is_flag=True, help="Bypass the in-memory read cache for this invocation")
@click.pass_context
def cli(ctx, host, port, token, no_cache):
    """CLI for Logseq knowledge graph - pages, journals, blocks, search, and graph analysis."""
    ctx.ensure_object(dict)
    api = LogseqAPI(host=host, port=port, token=token)
    if no_cache:
        api.cache_enabled = False
    ctx.obj["api"] = api


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


def _extract_backlink_names(refs) -> list:
    """Extract sorted page names from getPageLinkedReferences response.

    The native API returns a list of [page_dict, [block, ...]] pairs.
    We extract the page name from each pair and return a sorted list.
    """
    if not refs or not isinstance(refs, list):
        return []
    names = []
    for entry in refs:
        if isinstance(entry, (list, tuple)) and len(entry) >= 1:
            page_info = entry[0]
            if isinstance(page_info, dict):
                name = page_info.get("originalName") or page_info.get("name", "")
                if name:
                    names.append(name)
    return sorted(names)


def _is_properties_block(content: str) -> bool:
    """Check if block content is a Logseq properties block (key:: value lines)."""
    lines = content.strip().split("\n")
    return all(re.match(r"^[\w-]+::", line) for line in lines if line.strip())


def _blocks_to_markdown(blocks, indent=0):
    """Convert block tree to Logseq-compatible markdown.

    Properties blocks (top-level, all lines match 'key:: value') are rendered
    without bullet prefix to match Logseq's on-disk format.
    """
    lines = []
    prefix = "\t" * indent
    for block in blocks:
        content = block.get("content", "")
        if content:
            if indent == 0 and _is_properties_block(content):
                # Properties block: no bullet prefix, matches Logseq file format
                lines.append(content)
            else:
                lines.append(f"{prefix}- {content}")
        children = block.get("children", [])
        if children:
            lines.append(_blocks_to_markdown(children, indent + 1))
    return "\n".join(lines)


def _blocks_with_ids(blocks, indent=0):
    """Render block tree as ``<uuid>\\t<indent-tabs>\\t<content>`` lines.

    Allows downstream tools to extract a block UUID without parsing JSON.
    Indentation is encoded as a run of tab characters whose length matches
    the depth (matching ``_blocks_to_markdown``).
    """
    lines = []
    indent_str = "\t" * indent
    for block in blocks:
        uuid = block.get("uuid") or ""
        content = block.get("content", "")
        if content:
            lines.append(f"{uuid}\t{indent_str}\t{content}")
        children = block.get("children", [])
        if children:
            lines.append(_blocks_with_ids(children, indent + 1))
    return "\n".join(lines)


def _extract_section(blocks, heading_text):
    """Return the block matching heading_text (with its children), searched recursively.

    Uses :func:`normalize_heading` so renderer macros (e.g. ``{{renderer :todomaster}}``)
    and whitespace variations on the stored block do not prevent a match.
    """
    target = normalize_heading(heading_text)
    for block in blocks:
        content = block.get("content") or ""
        if normalize_heading(content) == target:
            return [block]
        children = block.get("children", [])
        if children:
            result = _extract_section(children, heading_text)
            if result:
                return result
    return []


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
                backlinks = _extract_backlink_names(refs)
            except Exception:
                backlinks = find_backlinks(api, page_name)
        if heading and blocks:
            blocks = _extract_section(blocks, heading)
            if not blocks:
                click.echo(f"Warning: heading '{heading}' not found in '{page_name}'", err=True)
        if resolve_refs and blocks:
            _resolve_refs_in_blocks(api, blocks)
        return {"page": page_name, "blocks": blocks, "backlinks": backlinks}

    results = [_fetch_one(p) for p in page]

    for result in results:
        if result["page"] in missing:
            result["exists"] = False

    if as_json:
        output(results if len(results) > 1 else results[0], True)
    else:
        for result in results:
            p, blocks, backlinks = result["page"], result["blocks"], result["backlinks"]
            absent = p in missing
            placeholder = "(page does not exist)" if absent else "(empty page)"
            if with_ids:
                click.echo(f"=== {p} ===\n")
                click.echo(_blocks_with_ids(blocks) if blocks else placeholder)
            elif output_format == "markdown":
                click.echo(_blocks_to_markdown(blocks) if blocks else placeholder)
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
@cli.command("get-block", epilog="""\b
Example:
  logseq-cli --token TOKEN get-block --id 12345678-90ab-cdef-1234-567890abcdef
Note:
  UUID accepts "((uuid))" or bare uuid form. Use get-page --resolve-refs for bulk.
""")
@click.option("--id", "block_id", required=True, help="Block UUID (with or without (()))")
@click.option("--no-children", is_flag=True, help="Exclude child blocks")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
@handle_connection_error
def get_block(ctx, block_id, no_children, as_json):
    """Get a block by UUID."""
    api = ctx.obj["api"]
    # Strip (( )) if present
    block_id = block_id.strip("()")
    include_children = not no_children
    block = api.get_block(block_id, include_children=include_children)

    if as_json:
        output(block, True)
    else:
        if block:
            # Metadata
            page_info = block.get("page")
            if isinstance(page_info, dict):
                click.echo(f"Page: {page_info.get('name') or page_info.get('id', '?')}")
            elif page_info is not None:
                click.echo(f"Page: {page_info}")
            parent_info = block.get("parent")
            if isinstance(parent_info, dict):
                click.echo(f"Parent: {parent_info.get('name') or parent_info.get('id', '?')}")
            elif parent_info is not None:
                click.echo(f"Parent: {parent_info}")
            created = block.get("createdAt") or block.get("created-at")
            updated = block.get("updatedAt") or block.get("updated-at")
            if created:
                click.echo(f"Created: {datetime.datetime.fromtimestamp(created / 1000).strftime('%Y-%m-%d %H:%M')}")
            if updated:
                click.echo(f"Updated: {datetime.datetime.fromtimestamp(updated / 1000).strftime('%Y-%m-%d %H:%M')}")
            click.echo()

            content = block.get("content", "")
            click.echo(content)
            children = block.get("children", [])
            if children:
                click.echo(process_blocks(children, indent=1))
        else:
            click.echo("Block not found.")


# ---------------------------------------------------------------------------
# 3b. find-block
# ---------------------------------------------------------------------------
@cli.command("find-block", epilog="""\b
Examples:
  logseq-cli --token TOKEN find-block --content "Tag-Support" --page "Project Alpha" --first
  logseq-cli --token TOKEN find-block --content "^### " --page "X" --regex
Note:
  Output gives uuid + page + content preview. Use --first to disambiguate; pipe to
  insert-block --child-of, update-block, remove-block downstream.
""")
@click.option("--content", required=True, help="Content text (substring match or regex with --regex)")
@click.option("--page", "--name", default=None, help="Restrict search to this page name")
@click.option("--regex", "use_regex", is_flag=True, help="Interpret --content as regex pattern")
@click.option("--first", "first_only", is_flag=True, help="Output only the first match")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
@handle_connection_error
def find_block(ctx, content, page, use_regex, first_only, as_json):
    """Find blocks by content substring or regex."""
    api = ctx.obj["api"]

    if use_regex:
        if page:
            page_lower = page.lower()
            query = (
                '[:find (pull ?b [:block/content :block/uuid {:block/page [:block/original-name :block/name]}])'
                f' :where [?p :block/name "{page_lower}"]'
                ' [?b :block/page ?p]'
                ' [?b :block/content _]]'
            )
        else:
            query = (
                '[:find (pull ?b [:block/content :block/uuid {:block/page [:block/original-name :block/name]}])'
                ' :where [?b :block/content _]]'
            )
        raw = api.datascript_query(query) or []
        pattern = re.compile(content)
        matches = [r[0] for r in raw if r and r[0] and pattern.search(r[0].get("content", ""))]
    else:
        content_escaped = content.replace('"', '\\"')
        if page:
            page_lower = page.lower()
            query = (
                '[:find (pull ?b [:block/content :block/uuid {:block/page [:block/original-name :block/name]}])'
                f' :where [?p :block/name "{page_lower}"]'
                ' [?b :block/page ?p]'
                ' [?b :block/content ?c]'
                f' [(clojure.string/includes? ?c "{content_escaped}")]]'
            )
        else:
            query = (
                '[:find (pull ?b [:block/content :block/uuid {:block/page [:block/original-name :block/name]}])'
                ' :where [?b :block/content ?c]'
                f' [(clojure.string/includes? ?c "{content_escaped}")]]'
            )
        raw = api.datascript_query(query) or []
        matches = [r[0] for r in raw if r and r[0]]

    if first_only:
        matches = matches[:1]

    if as_json:
        output(matches, True)
    else:
        if not matches:
            click.echo("No blocks found.")
        else:
            click.echo(f"Found {len(matches)} block(s):")
            for block in matches:
                uuid = block.get("uuid") or "?"
                preview = (block.get("content") or "")[:80].replace("\n", " ")
                page_info = block.get("page")
                page_name = ""
                if isinstance(page_info, dict):
                    page_name = page_info.get("original-name") or page_info.get("name") or ""
                click.echo(f"  uuid: {uuid}")
                if page_name:
                    click.echo(f"  page: {page_name}")
                click.echo(f"  content: {preview}")
                click.echo()


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
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
@handle_connection_error
def get_backlinks(ctx, page, as_json):
    """Find pages that link to the given page(s) (uses native Logseq API). Pass --name multiple times for batch."""
    api = ctx.obj["api"]

    def _fetch_one(page_name):
        try:
            refs = api.get_page_linked_references(page_name)
            return _extract_backlink_names(refs) if refs else []
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


def _count_unresolved_refs(blocks) -> int:
    """Recursively count ((uuid)) patterns in block content."""
    count = 0
    for block in blocks:
        content = block.get("content", "")
        if content and "((" in content:
            count += len(_BLOCK_REF_RE.findall(content))
        children = block.get("children", [])
        if children:
            count += _count_unresolved_refs(children)
    return count


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
@click.option("--tail", "tail", default=None, type=int, help="Only the newest N journal days of the range (applied before fetching)")
@click.option("--limit", "limit", default=None, type=int, help="Only the oldest N journal days of the range (applied before fetching)")
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

    if tail is not None and tail < 1:
        raise click.BadParameter("--tail must be >= 1")
    if limit is not None and limit < 1:
        raise click.BadParameter("--limit must be >= 1")
    if tail is not None and limit is not None:
        raise click.BadParameter("--tail and --limit are mutually exclusive")

    start = datetime.datetime.combine(parse_date_keyword(from_date), datetime.time())
    end = datetime.datetime.combine(parse_date_keyword(to_date), datetime.time())

    if start > end:
        raise click.BadParameter("--from must be before or equal to --to")

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
                blocks = _extract_section(blocks, heading)
            if resolve_refs and blocks:
                _resolve_refs_in_blocks(api, blocks)
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
        total_refs = sum(_count_unresolved_refs(e.get("blocks", [])) for e in entries)
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
                    click.echo(_blocks_to_markdown(entry["blocks"]))
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
@click.option("--days", default=None, type=int, help="Limit to pages modified in last N days")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
@handle_connection_error
def analyze_graph(ctx, days, as_json):
    """Analyze the knowledge graph structure."""
    api = ctx.obj["api"]
    pages = api.get_all_pages()

    todo_pattern = re.compile(r"\b(todo|later)\b|\[ \]", re.IGNORECASE)
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
        if ref_lower not in page_names:
            missing.append({"page": ref_lower, "references": count})
    missing.sort(key=lambda x: x["references"], reverse=True)

    # Underdeveloped: exists, has refs, but very short content
    underdeveloped = []
    for name_lower, original in page_names.items():
        refs = incoming_refs.get(name_lower, 0)
        length = page_content_lengths.get(name_lower, 0)
        if refs >= min_refs and length < 100:
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

    mood_positive = re.compile(r"\b(happy|great|excited|good|wonderful|productive|grateful)\b", re.IGNORECASE)
    mood_negative = re.compile(r"\b(sad|tired|stressed|frustrated|anxious|overwhelmed|bad)\b", re.IGNORECASE)
    mood_keyword = re.compile(r"(?:mood|feeling):\s*(\w+)", re.IGNORECASE)
    mood_indicator_patterns = [
        "mood:", "feeling:", "\U0001f60a", "\U0001f614", "\U0001f620", "\U0001f60c",
        "happy", "sad", "angry", "excited", "tired", "anxious",
    ]
    incomplete_task = re.compile(r"- \[ \]")
    complete_task = re.compile(r"- \[x\]", re.IGNORECASE)
    link_pattern = re.compile(r"\[\[(.*?)\]\]")
    project_pattern = re.compile(r"#project/(\S+)")
    habit_checkbox = re.compile(r"- \[[ x]\]")

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

        # Mood (original simple counters)
        if mood:
            mood_data = {"positive": 0, "negative": 0, "keywords": []}
            mood_data["positive"] = len(mood_positive.findall(content))
            mood_data["negative"] = len(mood_negative.findall(content))
            kw_matches = mood_keyword.findall(content)
            mood_data["keywords"] = kw_matches
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
                project_progress[proj_match.group(1)].append({
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


def _extract_param_from_request(req_lower, keywords, original_request):
    """Extract a dynamic parameter value from a natural language request.

    Removes matched keywords from the request to isolate the parameter value.
    Example: "links to Alice" with keyword "links to" -> "Alice"
    """
    remaining = original_request.strip()
    remaining_lower = req_lower.strip()
    # Remove matched keywords (longest first to avoid partial removal)
    for kw in sorted(keywords, key=len, reverse=True):
        idx = remaining_lower.find(kw)
        if idx != -1:
            remaining = remaining[:idx] + remaining[idx + len(kw):]
            remaining_lower = remaining_lower[:idx] + remaining_lower[idx + len(kw):]
    result = remaining.strip().strip('"').strip("'").strip()
    return result if result else None


def _print_results(results):
    """Print query results in human-readable format (max 20 items)."""
    if not isinstance(results, list):
        return
    for i, item in enumerate(results[:20]):
        if isinstance(item, list) and len(item) > 0:
            block = item[0]
            if isinstance(block, dict):
                name = block.get("name") or block.get("original-name") or block.get("content", "")[:80]
                click.echo(f"  {i+1}. {name}")
            else:
                click.echo(f"  {i+1}. {block}")
        elif isinstance(item, dict):
            name = item.get("name") or item.get("originalName") or item.get("content", "")[:80]
            click.echo(f"  {i+1}. {name}")
        else:
            click.echo(f"  {i+1}. {item}")


# ---------------------------------------------------------------------------
# 10. smart-query
# ---------------------------------------------------------------------------
@cli.command("smart-query", epilog="""\b
Examples:
  logseq-cli --token TOKEN smart-query --request "offene aufgaben"
  logseq-cli --token TOKEN smart-query --request '[:find ?n :where [?p :block/name ?n]]' --advanced
Note:
  Without --advanced: keyword-template match (fragile for complex queries).
  With --advanced: raw Datalog passes through untouched.
""")
@click.option("--request", required=True, help="Natural language query request (or raw Datalog with --advanced)")
@click.option("--include-query", is_flag=True, help="Include the generated Datalog query in output")
@click.option("--advanced", is_flag=True, help="Pass --request as raw Datalog query (bypass template matching)")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
@handle_connection_error
def smart_query(ctx, request, include_query, advanced, as_json):
    """Run smart Datalog queries via pattern matching on request keywords.

    Use --advanced to pass a raw Datalog query string directly via --request,
    bypassing all template matching. Without --advanced, natural language in
    --request is matched against pre-built query templates.
    """
    api = ctx.obj["api"]
    req_lower = request.lower()

    # --advanced mode: pass raw Datalog query directly to datascript_query
    if advanced:
        query_str = request
        description = "Advanced (raw Datalog query)"
        try:
            results = api.datascript_query(query_str)
        except Exception as e:
            results = []
            if not as_json:
                click.echo(f"Query error: {e}", err=True)

        result_data = {
            "request": request,
            "matched_template": "advanced",
            "description": description,
            "results_count": len(results) if isinstance(results, list) else 0,
            "results": results,
        }
        if include_query:
            result_data["query"] = query_str

        if as_json:
            output(result_data, True)
        else:
            click.echo(f"Query: {description}")
            if include_query:
                click.echo(f"Datalog: {query_str}")
            click.echo(f"Results: {result_data['results_count']}\n")
            _print_results(results)
        return

    # Pre-built query templates
    query_templates = {
        "recent": {
            "keywords": ["recent", "latest", "new", "last modified", "updated", "kürzlich", "zuletzt", "letzte", "neueste"],
            "query": '[:find (pull ?p [*]) :where [?p :block/name] [?p :block/updated-at ?u] [(> ?u {timestamp})]]',
            "description": "Recently modified pages",
        },
        "referenced": {
            "keywords": ["most referenced", "popular", "top pages", "most linked"],
            "query": '[:find ?name (count ?b) :where [?b :block/content ?c] [?p :block/name ?name] [(clojure.string/includes? ?c ?name)]]',
            "description": "Most referenced pages",
        },
        "tasks": {
            "keywords": ["todo", "task", "tasks", "incomplete", "pending", "aufgaben", "offene", "offen"],
            "query": '[:find (pull ?b [*]) :where [?b :block/marker ?m] [(contains? #{"TODO" "LATER" "NOW" "DOING"} ?m)]]',
            "description": "Open tasks",
        },
        "done": {
            "keywords": ["done", "completed", "finished", "erledigt", "fertig", "abgeschlossen"],
            "query": '[:find (pull ?b [*]) :where [?b :block/marker "DONE"]]',
            "description": "Completed tasks",
        },
        "journal": {
            "keywords": ["journal", "diary", "daily", "tagebuch"],
            "query": '[:find (pull ?p [*]) :where [?p :block/journal? true]]',
            "description": "Journal pages",
        },
        "properties": {
            "keywords": ["property", "properties", "type"],
            "query": '[:find (pull ?b [*]) :where [?b :block/properties ?p] [(not-empty ?p)]]',
            "description": "Blocks with properties",
        },
        "scheduled": {
            "keywords": ["scheduled", "deadline", "due", "geplant", "fällig", "termin"],
            "query": '[:find (pull ?b [*]) :where (or [?b :block/scheduled ?d] [?b :block/deadline ?d])]',
            "description": "Scheduled/deadline blocks",
        },
        "empty": {
            "keywords": ["empty", "blank", "no content", "leer", "ohne inhalt"],
            "query": '[:find (pull ?p [*]) :where [?p :block/name ?n] (not [?b :block/page ?p] [?b :block/content ?c] [(not= ?c "")])]',
            "description": "Empty pages",
        },
        "links-to": {
            "keywords": ["links to", "references", "mentions", "verlinkt", "referenziert"],
            "query": (
                '[:find (pull ?b [:block/content :block/uuid {:block/page [:block/original-name :block/name]}])'
                ' :where [?b :block/refs ?target] [?target :block/name "{page_name}"]]'
            ),
            "description": "Blocks linking to {page_name}",
            "extract_param": "page_name",
        },
        "created-today": {
            "keywords": ["created today", "today", "heute erstellt"],
            "query": (
                '[:find (pull ?b [:block/content :block/uuid {:block/page [:block/original-name :block/name]}])'
                ' :where [?b :block/created-at ?c] [(> ?c {today_start})]]'
            ),
            "description": "Blocks created today",
        },
        "tagged": {
            "keywords": ["tagged", "tag", "hashtag", "getaggt", "markiert"],
            "query": (
                '[:find (pull ?b [:block/content :block/uuid {:block/page [:block/original-name :block/name]}])'
                ' :where [?b :block/content ?c] [(clojure.string/includes? ?c "#{tag_name}")]]'
            ),
            "description": "Blocks tagged with #{tag_name}",
            "extract_param": "tag_name",
        },
        "long-content": {
            "keywords": ["long", "detailed", "ausfuehrlich", "ausführlich"],
            "query": (
                '[:find (pull ?b [:block/content :block/uuid {:block/page [:block/original-name :block/name]}])'
                ' :where [?b :block/content ?c] [(count ?c) ?len] [(> ?len 300)]]'
            ),
            "description": "Blocks with long content (>300 chars)",
        },
        "persons": {
            "keywords": ["person", "persons", "people", "personen", "kollegen"],
            "query": '[:find (pull ?p [*]) :where [?p :block/name] [?p :block/properties ?props] [(get ?props :type) ?t] [(= ?t "Person")]]',
            "description": "All person pages",
        },
        "projects": {
            "keywords": ["project", "projects", "projekte"],
            "query": '[:find (pull ?p [*]) :where [?p :block/name ?n] [(clojure.string/starts-with? ?n "projekte/")]]',
            "description": "All project pages",
        },
    }

    # Match query template
    best_match = None
    best_score = 0

    for key, template in query_templates.items():
        score = sum(1 for kw in template["keywords"] if kw in req_lower)
        if score > best_score:
            best_score = score
            best_match = key

    if not best_match:
        # Default: content search across all blocks, then fall back to page name search
        best_match = "content-search"

    if best_match == "content-search":
        # Improved fallback: search block content, then page names as last resort
        search_term = request.strip()
        content_query = (
            '[:find (pull ?b [:block/content :block/uuid {:block/page [:block/original-name :block/name]}])'
            ' :where [?b :block/content ?c]'
            f' [(clojure.string/includes? ?c "{search_term}")]]'
        )
        try:
            results = api.datascript_query(content_query)
            query_used = content_query
            description = f"Content search for '{search_term}'"
        except Exception:
            # Final fallback: page name search
            pages = api.get_all_pages()
            results = [
                p for p in pages
                if req_lower in (p.get("name") or "").lower()
            ]
            query_used = f"(page name search for '{request}')"
            description = "Page name search (content search failed)"
    else:
        template = query_templates[best_match]
        query_str = template["query"]

        # Handle timestamp placeholder
        if "{timestamp}" in query_str:
            ts = int((datetime.datetime.now() - datetime.timedelta(days=7)).timestamp() * 1000)
            query_str = query_str.replace("{timestamp}", str(ts))

        # Handle today_start placeholder
        if "{today_start}" in query_str:
            today = datetime.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            ts = int(today.timestamp() * 1000)
            query_str = query_str.replace("{today_start}", str(ts))

        # Handle dynamic parameter extraction (page_name, tag_name)
        extract_param = template.get("extract_param")
        if extract_param and f"{{{extract_param}}}" in query_str:
            param_value = _extract_param_from_request(req_lower, template["keywords"], request)
            if param_value:
                query_str = query_str.replace(f"{{{extract_param}}}", param_value)
                description = template["description"].replace(f"{{{extract_param}}}", param_value)
            else:
                query_str = query_str.replace(f"{{{extract_param}}}", request.strip())
                description = template["description"].replace(f"{{{extract_param}}}", request.strip())
        else:
            description = template["description"]

        try:
            results = api.datascript_query(query_str)
        except Exception as e:
            results = []
            if not as_json:
                click.echo(f"Query error: {e}", err=True)

        query_used = query_str

    result_data = {
        "request": request,
        "matched_template": best_match,
        "description": description,
        "results_count": len(results) if isinstance(results, list) else 0,
        "results": results,
    }
    if include_query:
        result_data["query"] = query_used

    if as_json:
        output(result_data, True)
    else:
        click.echo(f"Query: {description}")
        if include_query:
            click.echo(f"Datalog: {query_used}")
        click.echo(f"Results: {result_data['results_count']}\n")
        _print_results(results)


# ---------------------------------------------------------------------------
# 11. suggest-connections
# ---------------------------------------------------------------------------
@cli.command("suggest-connections", epilog="""\b
Example:
  logseq-cli --token TOKEN suggest-connections --min-confidence 0.7 --max-suggestions 10
""")
@click.option("--min-confidence", default=0.3, type=float, help="Minimum confidence score (0-1)")
@click.option("--max-suggestions", default=10, type=int, help="Maximum suggestions to return")
@click.option("--focus", default=None, help="Focus on specific page/topic")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
@handle_connection_error
def suggest_connections(ctx, min_confidence, max_suggestions, focus, as_json):
    """Suggest connections between pages based on shared topics."""
    api = ctx.obj["api"]
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

            score = len(intersection) / len(union)
            if score >= min_confidence:
                suggestions.append({
                    "page_a": page_a,
                    "page_b": page_b,
                    "confidence": round(score, 3),
                    "shared_topics": sorted(intersection),
                })

    suggestions.sort(key=lambda s: s["confidence"], reverse=True)
    suggestions = suggestions[:max_suggestions]

    result = {
        "suggestions": suggestions,
        "total_found": len(suggestions),
        "min_confidence": min_confidence,
    }

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
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
@handle_connection_error
def create_page(ctx, page, content, as_json):
    """Create a new page, optionally with initial content."""
    api = ctx.obj["api"]
    properties = {"journal?": True} if is_journal_date(page) else None
    result = api.create_page(page, properties)

    if content:
        api.append_block_in_page(page, content)

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
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
@handle_connection_error
def add_journal_entry(ctx, content, date, as_block, as_json):
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
    if not existing:
        api.create_page(page_name, {"journal?": True})

    content = strip_title_heading(content, page_name)

    if as_block:
        result = api.append_block_in_page(page_name, content)
        blocks_added = 1
    else:
        lines = [l.strip() for l in content.split("\n") if l.strip()]
        result = None
        for line in lines:
            result = api.append_block_in_page(page_name, line)
        blocks_added = len(lines)

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
  logseq-cli --token TOKEN add-journal-block --content "**$(date +%H:%M)** Meeting mit [[Bob]]"
  logseq-cli --token TOKEN add-journal-block --date 2026-05-07 --content "**14:30** Nachtrag"
  logseq-cli --token TOKEN add-journal-block --under-heading "## Meeting" --content "..."
  logseq-cli --token TOKEN add-journal-block --content "TODO A" --content "TODO B"   # batch
  logseq-cli --token TOKEN add-journal-block --under-heading "## Meeting" \\
                                              --upsert-heading "### [[Carol]]" --content "..."
Notes:
  Default heading from LOGSEQ_JOURNAL_HEADING env (e.g. "## Log").
  --upsert-heading replaces a placeholder block under --under-heading without needing UUID.
  Auto-detects tab-indented hierarchy in --content; no need to switch to add-journal-content.
""")
@click.option("--content", "contents", required=True, multiple=True, help="Block content (repeatable for batch: --content 'text1' --content 'text2')")
@click.option("--date", default=None, help="Date (YYYY-MM-DD), defaults to today")
@click.option("--under-heading", default=None, help="Insert as child of this heading (e.g. '## Log'). Creates heading if missing. Default from LOGSEQ_JOURNAL_HEADING env var, or top-level if unset.")
@click.option("--upsert-heading", default=None, help="Find child block matching this heading under --under-heading and update it; insert as new block if not found.")
@click.option("--top-level", is_flag=True, help="Add as top-level block (ignore --under-heading and env var)")
@click.option("--preserve-formatting/--no-preserve", default=True, help="Preserve content formatting")
@click.option("--dry-run", is_flag=True, help="Show what would be written without making changes")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
@handle_connection_error
def add_journal_block(ctx, contents, date, under_heading, upsert_heading, top_level, preserve_formatting, dry_run, as_json):
    """Add one or more blocks to a journal page.

    Pass --content multiple times for batch inserts under the same heading.
    This is the recommended command for journal entries. Inserts under the
    heading from LOGSEQ_JOURNAL_HEADING env var (default: top-level).

    Examples:
      logseq-cli add-journal-block --content "**14:30** Meeting notes"
      logseq-cli add-journal-block --under-heading "## Tasks" --content "TODO Task A" --content "TODO Task B"
      logseq-cli add-journal-block --date 2026-04-03 --content "Retroactive entry"
    """
    # Guard: reject flush (non-indented) newline bullets in ANY --content value.
    # Such content is neither detected as hierarchy (needs indentation) nor split
    # into siblings — it would silently become ONE block with raw "\n- " lines,
    # breaking the outline. Fail loudly with a fix instruction instead.
    for c in contents:
        if has_flush_newline_bullets(c):
            raise click.UsageError(
                "--content enthält mehrzeilige '- '-Bullets ohne Einrückung "
                "(Zeile 2+). Das wird NICHT als Hierarchie erkannt und landet "
                "als EIN Block mit rohen Newline-Bullets.\n"
                "  - Kinder gewollt?     -> Sub-Bullets mit Tab einrücken\n"
                "  - Geschwister gewollt? -> mehrere --content nutzen\n"
                "  - Voller Tree?        -> insert-block --tree"
            )

    # For single content: unwrap to scalar for backward-compatible logic below
    if len(contents) == 1:
        content = contents[0]
    else:
        content = None  # Will be handled in batch path below

    if top_level:
        under_heading = None
    elif under_heading is None:
        # Fall back to env var if no explicit --under-heading was given
        under_heading = os.environ.get("LOGSEQ_JOURNAL_HEADING")

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
        if not existing:
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
                output({"page": page_name, "date": str(d), "blocks": planned_total, "contents": list(contents), "dry_run": True}, True)
            else:
                if any_hierarchical:
                    click.echo("Note: Hierarchical content detected, using structured insertion", err=True)
                click.echo(f"[DRY RUN] Would add {planned_total} block(s) to journal: {page_name}")
                for c in contents:
                    click.echo(f"  {c[:80]}")
            return

        heading_uuid = find_or_create_heading(api, page_name, under_heading) if under_heading else None
        uuids = []
        for kind, payload in planned:
            if kind == "tree":
                if heading_uuid:
                    uuids.extend(insert_block_tree_with_uuids(api, payload, heading_uuid, strict=True))
                else:
                    # payload is the parsed tree; insert top nodes + children at page level
                    uuids.extend(insert_block_tree_at_page_top(api, payload, page_name))
            else:
                if heading_uuid:
                    r = api.insert_block(heading_uuid, payload, {"sibling": False})
                else:
                    r = api.append_block_in_page(page_name, payload)
                uuids.append(require_insert(r, "a journal block"))
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

    # Ensure journal page exists with journal property
    try:
        existing = api.get_page(page_name)
    except Exception:
        existing = None
    if not existing:
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
            if contains_hierarchical_content(content):
                tree = parse_hierarchical_content(content)
                if tree:
                    api.update_block(found_uuid, tree[0]["content"])
                    for sub in tree[0].get("children", []):
                        insert_block_tree(api, [sub], found_uuid)
                    n = count_blocks(tree)
                else:
                    api.update_block(found_uuid, content)
                    n = 1
            else:
                api.update_block(found_uuid, content)
                n = 1
            status = "updated"
        elif heading_uuid:
            if contains_hierarchical_content(content):
                tree = parse_hierarchical_content(content)
                created = insert_block_tree_with_uuids(api, tree, heading_uuid)
                n = len(created)
                root_uuid = created[0] if created else None
            else:
                r = api.insert_block(heading_uuid, content, {"sibling": False})
                n = 1
                root_uuid = r.get("uuid") if isinstance(r, dict) else None
            status = "created"
        else:
            r = api.append_block_in_page(page_name, content)
            n = 1
            root_uuid = r.get("uuid") if isinstance(r, dict) else None
            status = "top-level (heading not found)"

        position = f"upsert '{upsert_heading}' under '{under_heading}' ({status})"
        if as_json:
            output({"page": page_name, "date": str(d), "position": position, "blocks": n, **uuid_fields([u for u in [root_uuid] if u])}, True)
        else:
            click.echo(f"Added {n} block(s) to journal: {page_name} ({position})")
        return

    # Auto-detect hierarchical content and delegate to structured insertion
    if preserve_formatting and contains_hierarchical_content(content):
        click.echo("Note: Hierarchical content detected, using structured insertion", err=True)
        tree = parse_hierarchical_content(content)
        n = count_blocks(tree)
        position = f"under '{under_heading}'" if under_heading else "top-level"

        if dry_run:
            if as_json:
                output({"page": page_name, "date": str(d), "position": position, "blocks": n, "content": content, "dry_run": True}, True)
            else:
                click.echo(f"[DRY RUN] Would add {n} block(s) to journal: {page_name} ({position})")
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
            output({"page": page_name, "date": str(d), "position": position_desc, "content": content, "dry_run": True}, True)
        else:
            click.echo(f"[DRY RUN] Would add to journal: {page_name} ({position_desc})")
            click.echo(f"  {content}")
        return

    if under_heading:
        heading_uuid = find_or_create_heading(api, page_name, under_heading)
        if heading_uuid:
            result = api.insert_block(heading_uuid, content, {"sibling": False})
            position = f"under '{under_heading}'"
        else:
            result = api.append_block_in_page(page_name, content)
            position = "top-level (heading not found)"
            click.echo(f"Warning: Could not find or create '{under_heading}', added as top-level block", err=True)
    else:
        result = api.append_block_in_page(page_name, content)
        position = "top-level"

    if as_json:
        _u = result.get("uuid") if isinstance(result, dict) else (result if isinstance(result, str) else None)
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
    if top_level:
        under_heading = None
    elif under_heading is None:
        under_heading = os.environ.get("LOGSEQ_JOURNAL_HEADING")

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
  logseq-cli --token TOKEN add-note-content --page "Alice Example" --content "Body text"
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
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
@handle_connection_error
def add_note_content(ctx, page, content, create, under_heading, properties, as_json):
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

    if not existing and create:
        api.create_page(page)

    content = strip_title_heading(content, page)

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
  logseq-cli --token TOKEN update-block --id 12345678-... --content "Neuer Text"
Note:
  Use set-property/remove-property for properties — never edit them via update-block.
  Use set-todo-status to change TODO/DOING/DONE markers.
""")
@click.option("--id", "block_id", required=True, help="UUID of the block to update")
@click.option("--content", required=True, help="New content for the block")
@click.option("--dry-run", is_flag=True, help="Show the block that would be overwritten, without writing")
@click.option("--json", "as_json", is_flag=True, help="JSON output")
@click.pass_context
@handle_connection_error
def update_block(ctx, block_id, content, dry_run, as_json):
    """Update the content of an existing block."""
    api = ctx.obj["api"]
    clean_id = block_id.strip().replace("((", "").replace("))", "")

    # Verify block exists
    block = api.get_block(clean_id, include_children=False)
    if not block:
        fail(f"Block not found: {clean_id}", as_json=as_json, id=clean_id)

    old_content = block.get("content", "") if isinstance(block, dict) else ""

    if dry_run:
        if as_json:
            output({"id": clean_id, "old_content": old_content,
                    "new_content": content, "dry_run": True}, True)
        else:
            click.echo(f"[DRY RUN] Would overwrite block {clean_id}")
            if old_content:
                preview = old_content[:60] + ("..." if len(old_content) > 60 else "")
                click.echo(f"  was: {preview}")
            preview = content[:60] + ("..." if len(content) > 60 else "")
            click.echo(f"  now: {preview}")
        return

    api.update_block(clean_id, content)

    if as_json:
        output({"id": clean_id, "old_content": old_content, "new_content": content}, True)
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
# wrong guess. Register it as an alias
# so the guess works instead of erroring out.
cli.add_command(remove_block_cmd, "delete-block")


@cli.command("replace-text", epilog="""\b
Example:
  logseq-cli --token TOKEN replace-text --page "X" --find "alt" --replace "neu" --dry-run
  logseq-cli --token TOKEN replace-text --page "X" --find "alt" --replace "neu"
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
            if pattern.search(content):
                new_content = pattern.sub(replace_text, content)
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

    if as_json:
        output({"page": page, "replacements": len(replacements),
                "dry_run": dry_run, "matches": replacements}, True)
    else:
        if not replacements:
            click.echo(f"No matches for '{find_text}' in '{page}'.")
        else:
            action = "Would replace" if dry_run else "Replaced"
            click.echo(f"{action} {len(replacements)} block(s) in '{page}':")
            for r in replacements:
                old_preview = r["old"][:60] + ("..." if len(r["old"]) > 60 else "")
                new_preview = r["new"][:60] + ("..." if len(r["new"]) > 60 else "")
                click.echo(f"  {r['id'][:8]}..  {old_preview}")
                click.echo(f"         →  {new_preview}")


@cli.command("insert-block", epilog="""\b
Examples:
  logseq-cli --token TOKEN insert-block --child-of UUID --content "Sub-Block"
  logseq-cli --token TOKEN insert-block --after UUID --content "Sibling block"
  logseq-cli --token TOKEN insert-block --child-of UUID \\
    --tree "Parent\\n\\tChild1\\n\\tChild2\\n\\t\\tGrandchild"
  logseq-cli --token TOKEN insert-block --page "X" --top-level \\
    --tree '[{"content":"...","children":[{"content":"..."}]}]'
Notes:
  --tree accepts tab-indented text OR JSON (auto-detected). Use it instead of
  N×insert-block for hierarchies — single API roundtrip.
  --content and --tree are mutually exclusive.
  --child-of UUID also accepts hierarchical --content (same tab-indent format).
""")
@click.option("--page", "--name", default=None, help="Page name (append to end of page)")
@click.option("--after", default=None, help="UUID of block to insert after (as sibling)")
@click.option("--before", default=None, help="UUID of block to insert before (as sibling)")
@click.option("--child-of", default=None, help="UUID of parent block (insert as child)")
@click.option("--top-level", is_flag=True, help="With --page and --tree: insert at page top-level")
@click.option("--content", default=None, help="Content for the new block")
@click.option("--tree", "tree_input", default=None, help="Tab-indented hierarchy or JSON array of {content, children} nodes")
@click.option("--property", "properties", multiple=True, help="Set KEY=VALUE property on the created (root) block; repeatable")
@click.option("--dry-run", is_flag=True, help="Show what would be inserted (block count + position) without writing")
@click.option("--json", "as_json", is_flag=True, help="JSON output")
@click.pass_context
@handle_connection_error
def insert_block_cmd(ctx, page, after, before, child_of, top_level, content, tree_input, properties, dry_run, as_json):
    """Insert a block (or tree of blocks) at a specific position."""
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

    if tree_input is not None:
        if content is not None:
            click.echo("Specify either --content or --tree, not both.", err=True)
            sys.exit(1)
        tree = parse_tree_input(tree_input)
        if not tree:
            click.echo("Tree input is empty.", err=True)
            sys.exit(1)

        # Resolve target + position first (no writes), so --dry-run can report
        # the plan and bail before touching the graph.
        if child_of:
            clean_id = child_of.strip().replace("((", "").replace("))", "")
            position = f"child of {clean_id[:8]}..."
            do_insert = lambda: insert_block_tree_with_uuids(api, tree, clean_id, strict=True)
        elif after:
            clean_id = after.strip().replace("((", "").replace("))", "")
            position = f"after {clean_id[:8]}..."
            do_insert = lambda: insert_block_tree_as_siblings(api, tree, clean_id, before=False)
        elif before:
            clean_id = before.strip().replace("((", "").replace("))", "")
            position = f"before {clean_id[:8]}..."
            do_insert = lambda: insert_block_tree_as_siblings(api, tree, clean_id, before=True)
        elif page and top_level:
            position = f"top-level of '{page}'"
            do_insert = lambda: insert_block_tree_at_page_top(api, tree, page)
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
            for u in uuids:
                click.echo(f"  uuid: {u}")
            for key, value in applied.items():
                click.echo(f"  {key}:: {value}")
        return

    if content is None:
        click.echo("Specify --content or --tree.", err=True)
        sys.exit(1)

    targets = sum(1 for x in [page, after, before, child_of] if x)
    if targets == 0:
        click.echo("Specify one of: --page, --after, --before, --child-of", err=True)
        sys.exit(1)
    if targets > 1:
        click.echo("Specify only one of: --page, --after, --before, --child-of", err=True)
        sys.exit(1)

    result = None
    position = ""
    new_uuid = None
    hierarchical = contains_hierarchical_content(content)

    if dry_run:
        planned = count_blocks(parse_hierarchical_content(content)) if hierarchical else 1
        target = page or (f"after {after[:8]}..." if after else
                          f"before {before[:8]}..." if before else
                          f"child of {child_of[:8]}...")
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
        if hierarchical:
            tree = parse_hierarchical_content(content)
            uuids = insert_block_tree_with_uuids(api, tree, clean_id, strict=True)
            new_uuid = uuids[0] if uuids else None
            result = {"blocks_added": len(uuids), "uuids": uuids}
            position = f"child of {clean_id[:8]}... ({len(uuids)} block(s))"
        else:
            result = api.insert_block(clean_id, content, {"sibling": False})
            new_uuid = require_insert(result, f"a child of {clean_id[:8]}...")
            position = f"child of {clean_id[:8]}..."

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
                                          --under-heading "## Offene TODOs"
Note:
  Default target: today's journal. Auto-creates the journal page if missing.
""")
@click.option("--source-id", required=True, help="UUID of the block to reference")
@click.option("--journal-date", default=None, help="Target journal date (YYYY-MM-DD), defaults to today")
@click.option("--page", "--name", default=None, help="Target page name (alternative to --journal-date)")
@click.option("--under-heading", default=None, help="Insert under this heading. Defaults to LOGSEQ_JOURNAL_HEADING env var, or top-level.")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
@handle_connection_error
def add_block_ref(ctx, source_id, journal_date, page, under_heading, as_json):
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
            api.create_page(page, {"journal?": True})

    source_id = source_id.strip("()")
    ref_content = f"(({source_id}))"

    if under_heading is None:
        under_heading = os.environ.get("LOGSEQ_JOURNAL_HEADING")

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

    new_uuid = result.get("uuid") if isinstance(result, dict) else None

    if as_json:
        output({"source_id": source_id, "ref": ref_content, "page": page, "position": position, "uuid": new_uuid}, True)
    else:
        click.echo(f"Added block-ref {position}")
        click.echo(f"  {ref_content}")
        if new_uuid:
            click.echo(f"  uuid: {new_uuid}")


# ---------------------------------------------------------------------------
# 21. get-todos
# ---------------------------------------------------------------------------
@cli.command("get-todos", epilog="""\b
Examples:
  logseq-cli --token TOKEN get-todos --status TODO --status DOING
  logseq-cli --token TOKEN get-todos --page "Projects" --tag urgent
  logseq-cli --token TOKEN get-todos --from 2026-05-01 --to 2026-05-31 --include-done
Notes:
  --status repeatable. Default: TODO, DOING, NOW, LATER (no DONE).
  Returns ORIGINAL blocks only — TODO Block-Refs ((uuid)) inside journals are NOT listed.
  Plain-text output: "MARKER [Page] preview" — page name inline, no grouping needed.
""")
@click.option("--status", multiple=True, default=("TODO", "DOING", "NOW", "LATER"),
              help="Task status to include (repeatable, default: TODO DOING NOW LATER)")
@click.option("--page", "--name", default=None, help="Filter by page name (substring, case-insensitive)")
@click.option("--tag", default=None, help="Filter by hashtag (e.g. 'urgent', without #)")
@click.option("--from", "from_date", default=None, help="Only TODOs from journal pages on or after this date (YYYY-MM-DD or 'today'/'yesterday'/'tomorrow'). Non-journal pages are always included.")
@click.option("--to", "to_date", default=None, help="Only TODOs from journal pages on or before this date (YYYY-MM-DD or 'today'/'yesterday'/'tomorrow'). Non-journal pages are always included.")
@click.option("--include-done", is_flag=True, help="Also include DONE tasks")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
@handle_connection_error
def get_todos(ctx, status, page, tag, from_date, to_date, include_done, as_json):
    """List all TODOs/tasks in the graph."""
    api = ctx.obj["api"]

    markers = set(s.upper() for s in status)
    if include_done:
        markers.add("DONE")

    markers_str = " ".join(f'"{m}"' for m in sorted(markers))
    query = (
        '[:find (pull ?b [:block/content :block/marker :block/uuid]) '
        '(pull ?p [:block/original-name :block/name :block/journal-day]) '
        ':where [?b :block/marker ?m] '
        f'[(contains? #{{{markers_str}}} ?m)] '
        '[?b :block/page ?p]]'
    )
    results = api.datascript_query(query)

    todos = []
    for block_data, page_data in results:
        content = block_data.get("content", "")
        marker = block_data.get("marker", "")
        uuid = block_data.get("uuid", "")
        page_name = page_data.get("original-name") or page_data.get("name", "")
        journal_day = page_data.get("journal-day") or page_data.get("journalDay")

        # Strip properties from content (lines with key:: value)
        content_lines = [l for l in content.split("\n") if not re.match(r"^\w[\w-]*::\s", l)]
        clean_content = "\n".join(content_lines).strip()
        # Strip leading marker from content (e.g. "TODO some task" -> "some task")
        clean_content = re.sub(r"^(TODO|DOING|DONE|NOW|LATER|WAITING|CANCELLED)\s+", "", clean_content)

        todos.append({
            "marker": marker,
            "content": clean_content,
            "page": page_name,
            "uuid": uuid,
            "_journal_day": journal_day,
        })

    # Filter by page if requested
    if page:
        page_lower = page.lower()
        todos = [t for t in todos if page_lower in t["page"].lower()]

    # Filter by tag if requested
    if tag:
        tag_pattern = re.compile(rf"#\b{re.escape(tag)}\b", re.IGNORECASE)
        todos = [t for t in todos if tag_pattern.search(t["content"])]

    # Filter by date range (journal pages only; non-journal pages always pass through)
    if from_date or to_date:
        date_start = (
            datetime.datetime.combine(parse_date_keyword(from_date), datetime.time())
            if from_date else None
        )
        date_end = (
            datetime.datetime.combine(parse_date_keyword(to_date), datetime.time())
            if to_date else None
        )
        filtered = []
        for t in todos:
            jd = t.get("_journal_day")
            if jd is None:
                filtered.append(t)
                continue
            try:
                d = journal_day_to_date(jd)
                dt = datetime.datetime.combine(d, datetime.time())
                if date_start and dt < date_start:
                    continue
                if date_end and dt > date_end:
                    continue
                filtered.append(t)
            except (ValueError, TypeError):
                filtered.append(t)
        todos = filtered

    # Strip internal _journal_day before output
    for t in todos:
        t.pop("_journal_day", None)

    # Sort: DOING/NOW first, then by page
    marker_order = {"DOING": 0, "NOW": 1, "TODO": 2, "LATER": 3, "DONE": 4}
    todos.sort(key=lambda t: (marker_order.get(t["marker"], 9), t["page"].lower()))

    if as_json:
        output({"todos": todos, "count": len(todos)}, True)
    else:
        if not todos:
            click.echo("No tasks found.")
        else:
            click.echo(f"Tasks ({len(todos)}):\n")
            for t in todos:
                preview = t["content"][:100] + ("..." if len(t["content"]) > 100 else "")
                click.echo(f"  {t['marker']} [{t['page']}] {preview}")


# ---------------------------------------------------------------------------
# 21b. set-todo-status
# ---------------------------------------------------------------------------
@cli.command("set-todo-status", epilog="""\b
Examples:
  logseq-cli --token TOKEN set-todo-status --id UUID --status DONE
  logseq-cli --token TOKEN set-todo-status --content "ship the parser" \\
                                            --page "Project Alpha" --status DONE
  logseq-cli --token TOKEN set-todo-status --id JOURNAL-UUID --status DONE --follow-refs
Notes:
  Status values: TODO, DOING, DONE, LATER, NOW, CANCELED.
  --follow-refs: when block is a ((uuid)) ref to a project page, updates the original.
  Prefer this over replace-text for marker changes — 1 call, deterministic.
""")
@click.option("--id", "block_id", default=None, help="Block UUID (find by UUID)")
@click.option("--content", default=None, help="Content substring to find the block (used with --page)")
@click.option("--page", "--name", default=None, help="Page to search in (used with --content)")
@click.option("--status", required=True,
              type=click.Choice(["TODO", "DOING", "DONE", "LATER", "NOW", "CANCELED"]),
              help="New task status")
@click.option("--follow-refs", is_flag=True,
              help="If the block content is a ((uuid)) reference, follow it and update the original block instead.")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
@handle_connection_error
def set_todo_status(ctx, block_id, content, page, status, follow_refs, as_json):
    """Update the status of a TODO block (e.g. TODO → DONE).

    Identify the block either by UUID (--id) or by content substring + page (--content + --page).
    Use --follow-refs when the block is a ((uuid)) reference in a journal and the original block
    lives on a project page.

    Examples:
      logseq-cli set-todo-status --id UUID --status DONE
      logseq-cli set-todo-status --content "ship the parser" --page "Project Alpha" --status DONE
      logseq-cli set-todo-status --id JOURNAL-REF-UUID --status DONE --follow-refs
    """
    api = ctx.obj["api"]

    if not block_id and not (content and page):
        click.echo("Specify either --id or both --content and --page.", err=True)
        sys.exit(1)

    # Resolve UUID via content search if needed
    if not block_id:
        content_escaped = content.replace('"', '\\"')
        page_lower = page.lower()
        query = (
            '[:find (pull ?b [:block/content :block/uuid]) '
            f':where [?p :block/name "{page_lower}"] '
            '[?b :block/page ?p] '
            '[?b :block/content ?c] '
            f'[(clojure.string/includes? ?c "{content_escaped}")]]'
        )
        raw = api.datascript_query(query) or []
        matches = [r[0] for r in raw if r and r[0]]
        # Prefer blocks that actually have a TODO marker
        todo_matches = [m for m in matches if m.get("content", "").split()[0:1] and
                        m.get("content", "").split()[0].upper() in _TODO_MARKERS]
        candidates = todo_matches or matches
        if not candidates:
            click.echo(f"No block found matching '{content}' on page '{page}'.", err=True)
            sys.exit(1)
        block_id = candidates[0].get("uuid")
        old_content = candidates[0].get("content", "")
    else:
        block_id = block_id.strip("()")
        block = api.get_block(block_id, include_children=False)
        if not block:
            click.echo(f"Block {block_id} not found.", err=True)
            sys.exit(1)
        old_content = block.get("content", "")

    # --follow-refs: if block content is just a ((uuid)) reference, update the referenced block
    if follow_refs:
        stripped = old_content.strip()
        ref_match = _BLOCK_REF_RE.fullmatch(stripped)
        if ref_match:
            ref_uuid = ref_match.group(1)
            ref_block = api.get_block(ref_uuid, include_children=False)
            if ref_block:
                block_id = ref_uuid
                old_content = ref_block.get("content", "")
            else:
                click.echo(f"Warning: referenced block {ref_uuid} not found, updating original.", err=True)

    new_content = _swap_todo_marker(old_content, status)
    if new_content == old_content:
        if as_json:
            output({"uuid": block_id, "status": "unchanged", "content": old_content}, True)
        else:
            click.echo(f"No change (block already has status or no marker found).")
        return

    api.update_block(block_id, new_content)

    if as_json:
        output({"uuid": block_id, "old": old_content, "new": new_content, "status": status}, True)
    else:
        click.echo(f"Updated: {old_content[:60]}{'...' if len(old_content) > 60 else ''}")
        click.echo(f"      → {new_content[:60]}{'...' if len(new_content) > 60 else ''}")


# ---------------------------------------------------------------------------
# 22. get-properties
# ---------------------------------------------------------------------------
@cli.command("get-properties", epilog="""\b
Examples:
  logseq-cli --token TOKEN get-properties --name "Alice Example"
  logseq-cli --token TOKEN get-properties --name "Alice" --property "team"
""")
@click.option("--page", "--name", required=True, help="Page name")
@click.option("--property", "prop_name", default=None, help="Get a specific property by name")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
@handle_connection_error
def get_properties(ctx, page, prop_name, as_json):
    """Get properties of a page."""
    api = ctx.obj["api"]
    page_data = api.get_page(page)

    if not page_data:
        fail(f"Page '{page}' not found.", as_json=as_json, page=page)

    properties = page_data.get("properties") or {}
    text_values = page_data.get("propertiesTextValues") or {}
    page_name = page_data.get("originalName") or page_data.get("name", page)

    # Logseq does not always expose page properties on the page object itself:
    # for pages written via set-property they live on the first block instead
    # (the property block). Without this fallback the command reported
    # "No properties" for pages whose properties were perfectly intact on disk,
    # which is what made set-property look like it had silently failed.
    if not properties:
        try:
            blocks = api.get_page_blocks_tree(page) or []
        except Exception:
            blocks = []
        if blocks:
            first = blocks[0] or {}
            block_props = first.get("properties") or {}
            if block_props:
                properties = block_props
                text_values = first.get("propertiesTextValues") or text_values

    if prop_name:
        prop_lower = prop_name.lower()
        # Properties are stored lowercase
        value = properties.get(prop_lower)
        text_value = text_values.get(prop_lower)
        if value is None:
            fail(f"Property '{prop_name}' not found on '{page_name}'.",
                 as_json=as_json, page=page_name, property=prop_name)

        if as_json:
            output({"page": page_name, "property": prop_lower, "value": value, "text": text_value}, True)
        else:
            click.echo(text_value or value)
    else:
        if as_json:
            output({"page": page_name, "properties": properties, "text_values": text_values}, True)
        else:
            if not properties:
                click.echo(f"No properties on '{page_name}'.")
            else:
                click.echo(f"Properties of '{page_name}':\n")
                for key in sorted(properties.keys()):
                    display = text_values.get(key, properties[key])
                    click.echo(f"  {key}:: {display}")


# ---------------------------------------------------------------------------
# 23. set-property
# ---------------------------------------------------------------------------
@cli.command("set-property", epilog="""\b
Examples:
  logseq-cli --token TOKEN set-property --name "Alice" --key "team" --value "[[Platform]]"
  logseq-cli --token TOKEN set-property --name "X" --key "type" --value "Person"
Note:
  Properties land at page-top (above first block). NEVER use update-block for
  properties — that creates a text-block, not a real property.
  Verify with: get-properties --name X
""")
@click.option("--page", "--name", required=True, help="Page name")
@click.option("--key", required=True, help="Property key (e.g. 'type', 'team', 'role')")
@click.option("--value", required=True, help="Property value")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
@handle_connection_error
def set_property(ctx, page, key, value, as_json):
    """Set or update a property on a page's first block."""
    api = ctx.obj["api"]

    # Get page blocks to find the first block (properties block)
    blocks = api.get_page_blocks_tree(page)
    if not blocks:
        fail(f"Page '{page}' not found or has no blocks", as_json=as_json, page=page)

    first_block = blocks[0]
    block_uuid = first_block.get("uuid")
    if not block_uuid:
        fail("Could not find block UUID", as_json=as_json, page=page)

    # Auto-detect value type (shared with set-block-property / --property)
    value = coerce_property_value(value)

    api.upsert_block_property(str(block_uuid), key, value)

    result = {"page": page, "property": key, "value": value, "status": "updated"}
    if as_json:
        output(result, True)
    else:
        click.echo(f"Set '{key}:: {value}' on page '{page}'")


# ---------------------------------------------------------------------------
# 24. remove-property
# ---------------------------------------------------------------------------
@cli.command("remove-property", epilog="""\b
Example:
  logseq-cli --token TOKEN remove-property --name "X" --key "deprecated_key"
""")
@click.option("--page", "--name", required=True, help="Page name")
@click.option("--key", required=True, help="Property key to remove")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
@handle_connection_error
def remove_property(ctx, page, key, as_json):
    """Remove a property from a page's first block."""
    api = ctx.obj["api"]

    blocks = api.get_page_blocks_tree(page)
    if not blocks:
        fail(f"Page '{page}' not found or has no blocks", as_json=as_json, page=page)

    first_block = blocks[0]
    block_uuid = first_block.get("uuid")
    if not block_uuid:
        fail("Could not find block UUID", as_json=as_json, page=page)

    api.remove_block_property(str(block_uuid), key)

    result = {"page": page, "property": key, "status": "removed"}
    if as_json:
        output(result, True)
    else:
        click.echo(f"Removed '{key}' from page '{page}'")


# ---------------------------------------------------------------------------
# 25. set-block-property
# ---------------------------------------------------------------------------
@cli.command("set-block-property", epilog="""\b
Example:
  logseq-cli --token TOKEN set-block-property --id UUID --key "id" --value "abc-123"
""")
@click.option("--id", "block_id", required=True, help="Block UUID")
@click.option("--key", required=True, help="Property key")
@click.option("--value", required=True, help="Property value")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
@handle_connection_error
def set_block_property(ctx, block_id, key, value, as_json):
    """Set or update a property on a specific block."""
    api = ctx.obj["api"]

    # Auto-detect value type (shared coercion with the inline --property option)
    value = coerce_property_value(value)

    api.upsert_block_property(block_id, key, value)

    result = {"block": block_id, "property": key, "value": value, "status": "updated"}
    if as_json:
        output(result, True)
    else:
        click.echo(f"Set '{key}:: {value}' on block '{block_id}'")


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
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
@handle_connection_error
def rename_page(ctx, page, new_name, as_json):
    """Rename a page (updates all references across the graph)."""
    api = ctx.obj["api"]

    # Verify page exists first
    page_data = api.get_page(page)
    if not page_data:
        fail(f"Page '{page}' not found", as_json=as_json, page=page)

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

    try:
        blocks = api.get_page_blocks_tree(page) or []
    except Exception:
        blocks = []
    block_count = count_blocks(blocks)

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

    result = {"page": page, "status": "deleted", "blocks": block_count}
    if as_json:
        output(result, True)
    else:
        click.echo(f"Deleted page '{page}' ({block_count} block(s))")


# ---------------------------------------------------------------------------
# 28. query-pages-by-property
# ---------------------------------------------------------------------------
@cli.command("query-pages-by-property", epilog="""\b
Examples:
  logseq-cli --token TOKEN query-pages-by-property --key "type" --value "Person"
  logseq-cli --token TOKEN query-pages-by-property --key "team"
Note:
  Without --value: lists all pages that have the key (with their values).
  With --value: exact-match filter.
""")
@click.option("--key", required=True, help="Property key to filter by (e.g. 'type', 'team', 'role')")
@click.option("--value", default=None, help="Property value to match (omit to find all pages with this key)")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
@handle_connection_error
def query_pages_by_property(ctx, key, value, as_json):
    """Find pages by property key/value (e.g. --key type --value Person)."""
    api = ctx.obj["api"]

    if value:
        # Query pages where property key matches value
        query = f'''[:find (pull ?p [:block/name :block/original-name :block/properties])
                     :where
                     [?p :block/name]
                     [?p :block/properties ?props]
                     [(get ?props :{key}) ?v]
                     [(= ?v "{value}")]]'''
    else:
        # Query pages that have this property key (any value)
        query = f'''[:find (pull ?p [:block/name :block/original-name :block/properties])
                     :where
                     [?p :block/name]
                     [?p :block/properties ?props]
                     [(get ?props :{key}) ?v]]'''

    try:
        results = api.datascript_query(query)
    except Exception as e:
        # Fallback: brute force via get_all_pages
        click.echo(f"Datalog query failed ({e}), falling back to page scan...", err=True)
        pages = api.get_all_pages()
        results = []
        for p in pages:
            props = p.get("properties", {})
            if key in props:
                if value is None or str(props[key]) == value:
                    results.append([p])

    # Extract page names from results
    pages_found = []
    for item in results:
        if isinstance(item, list) and len(item) > 0:
            page = item[0]
            if isinstance(page, dict):
                name = page.get("original-name") or page.get("name", "?")
                props = page.get("properties", {})
                prop_value = props.get(key, "")
                pages_found.append({"name": name, "value": str(prop_value)})
        elif isinstance(item, dict):
            name = item.get("original-name") or item.get("name", "?")
            props = item.get("properties", {})
            prop_value = props.get(key, "")
            pages_found.append({"name": name, "value": str(prop_value)})

    pages_found.sort(key=lambda x: x["name"].lower())

    result_data = {
        "key": key,
        "value": value,
        "count": len(pages_found),
        "pages": pages_found,
    }

    if as_json:
        output(result_data, True)
    else:
        filter_desc = f"{key}:: {value}" if value else f"{key}:: *"
        click.echo(f"Pages with {filter_desc} ({len(pages_found)}):\n")
        for p in pages_found:
            if value:
                click.echo(f"  {p['name']}")
            else:
                click.echo(f"  {p['name']} ({key}:: {p['value']})")


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

    def _copy_tree(block, parent_uuid=None):
        content = block.get("content", "")
        if parent_uuid:
            result = api.insert_block(parent_uuid, content, {"sibling": False})
        else:
            result = api.append_block_in_page(to_page, content)
        new_uuid = None
        if isinstance(result, dict):
            new_uuid = result.get("uuid")
        elif isinstance(result, str):
            new_uuid = result
        copied = 1
        for child in block.get("children", []):
            if new_uuid:
                copied += _copy_tree(child, new_uuid)
        return copied

    count = _copy_tree(source)

    if remove:
        api.remove_block(block_id)

    action = "Moved" if remove else "Copied"
    result_data = {"action": action.lower(), "blocks": count, "to_page": to_page, "source_id": block_id}

    if as_json:
        output(result_data, True)
    else:
        click.echo(f"{action} {count} block(s) to '{to_page}'.")


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
        inbound = _extract_backlink_names(refs)
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
def _port_has_listener(host: str, port: str, timeout: float = 2.0) -> bool:
    """True if something accepts TCP connections on host:port."""
    import socket
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except (OSError, ValueError):
        return False


def _logseq_process_running() -> "bool | None":
    """True/False if a Logseq desktop process is detectable, None if unknown.

    Best-effort and platform-dependent: used only to tell "app not running" from
    "app running but its HTTP API is off", which is the distinction that costs
    the most time to work out by hand.
    """
    import shutil
    import subprocess
    if not shutil.which("pgrep"):
        return None
    try:
        for pattern in ("Logseq", "logseq"):
            res = subprocess.run(["pgrep", "-x", pattern],
                                 capture_output=True, timeout=5)
            if res.returncode == 0:
                return True
        return False
    except (OSError, subprocess.SubprocessError):
        return None


@cli.command("doctor", epilog="""\b
Examples:
  logseq-cli --token TOKEN doctor
  logseq-cli --token TOKEN doctor --json
Note:
  Read-only. Exit 0 = ready to read and write, 1 = something is wrong.
  Distinguishes "Logseq not running" from "running but HTTP API off" and
  from "API up but token rejected" - each needs a different fix.
""")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
def doctor(ctx, as_json):
    """Check connectivity, auth and graph access in one call."""
    api = ctx.obj["api"]
    checks = []
    remedy = None

    def add(name, ok, detail):
        checks.append({"check": name, "ok": ok, "detail": detail})

    # 1. Is anything listening? Separates "app closed" from "API disabled",
    #    the exact ambiguity that turned a real outage into a manual hunt.
    listener = _port_has_listener(api.host, api.port)
    add("port", listener,
        f"{api.host}:{api.port} " + ("accepting connections" if listener else "no listener"))

    if not listener:
        proc = _logseq_process_running()
        if proc is True:
            add("process", False,
                "Logseq is running but nothing listens on the API port")
            remedy = ("Logseq runs, but its HTTP API is off or bound elsewhere. "
                      "Enable it in Logseq: Settings -> Features -> HTTP APIs Server, "
                      "then start the server and confirm the port.")
        elif proc is False:
            add("process", False, "no Logseq process found")
            remedy = "Logseq is not running. Start it, then enable the HTTP API server."
        else:
            add("process", None, "process state unknown (pgrep unavailable)")
            remedy = (f"Nothing listens on {api.host}:{api.port}. Check that Logseq runs "
                      "and its HTTP API server is enabled.")

    # 2. Token: only meaningful once the port answers.
    token_set = bool(api.token)
    if listener:
        add("token", token_set,
            "token provided" if token_set else "no token (--token or LOGSEQ_TOKEN)")

    # 3. Live API call. This is what actually proves usability.
    graph = None
    if listener:
        try:
            configs = api.call("logseq.App.getUserConfigs")
            add("api", True, "API responded")
            if isinstance(configs, dict):
                graph = configs.get("currentGraph") or configs.get("preferredWorkflow")
        except requests.HTTPError as e:
            code = e.response.status_code if e.response is not None else "?"
            add("api", False, f"HTTP {code}")
            if code == 401:
                # Distinguish "none supplied" from "supplied but wrong": the
                # first is a missing flag, the second a wrong value.
                remedy = (
                    "No token was supplied. Pass the value from Logseq's API "
                    "settings via --token or the LOGSEQ_TOKEN env var."
                    if not token_set else
                    "The API rejected the token. Check that it matches the value "
                    "in Logseq: Settings -> Features -> HTTP APIs Server."
                )
            else:
                remedy = f"API answered HTTP {code}. Check the Logseq API settings."
        except requests.RequestException as e:
            add("api", False, f"{type(e).__name__}: {e}")
            remedy = "Port is open but the API did not answer. Is another service on that port?"
        except Exception as e:  # noqa: BLE001 - doctor must never crash
            add("api", False, f"{type(e).__name__}: {e}")
            remedy = "Unexpected error talking to the API."

    # 4. Graph read: proves a graph is actually loaded, not just the API alive.
    if any(c["check"] == "api" and c["ok"] for c in checks):
        try:
            pages = api.get_all_pages()
            count = len(pages) if isinstance(pages, list) else 0
            add("graph", count > 0, f"{count} page(s) visible")
            if count == 0:
                remedy = "API works but no pages are visible. Is a graph open in Logseq?"
        except Exception as e:  # noqa: BLE001
            add("graph", False, f"{type(e).__name__}: {e}")
            remedy = "API works but the graph could not be read."

    healthy = all(c["ok"] for c in checks if c["ok"] is not None)

    result = {
        "healthy": healthy,
        "endpoint": api.base_url,
        "version": _resolve_version(),
        "checks": checks,
    }
    if graph:
        result["graph"] = graph
    if remedy:
        result["remedy"] = remedy

    if as_json:
        output(result, True)
    else:
        click.echo(f"logseq-cli {result['version']}  ->  {api.base_url}")
        for c in checks:
            mark = "ok  " if c["ok"] else ("??  " if c["ok"] is None else "FAIL")
            click.echo(f"  [{mark}] {c['check']}: {c['detail']}")
        if graph:
            click.echo(f"  graph: {graph}")
        click.echo()
        if healthy:
            click.echo("Ready: reads and writes should work.")
        else:
            click.echo("Not ready.")
            if remedy:
                click.echo(f"  {remedy}")

    if not healthy:
        sys.exit(1)


def main():
    cli()


if __name__ == "__main__":
    main()
