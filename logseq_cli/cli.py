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

from logseq_cli.api import LogseqAPI, DatalogQueryError
from logseq_cli.config import (
    ConfigError,
    config_search_paths,
    get,
    load_config,
    require,
    resolve_heading,
)
from logseq_cli.datalog import (
    InvalidKeywordError,
    edn_keyword,
    edn_string,
    page_name_literal,
)
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
    read_content_file,
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


def handle_connection_error(func):
    """Catch transport-level errors and report them like every other failure.

    These two are what a caller hits first: Logseq not running, or a wrong
    token. Reporting them as prose while ``--json`` was asked for would hand an
    agent unparseable text exactly at first contact, so they go through
    :func:`fail`, which honours ``--json`` and keeps errors on stderr.

    ``as_json`` is read from the wrapped command's kwargs; Click passes every
    option by name, so it is there whenever the command declares the flag.
    """
    def wrapper(*args, **kwargs):
        as_json = bool(kwargs.get("as_json"))
        try:
            return func(*args, **kwargs)
        except requests.ConnectionError:
            fail(
                "Cannot connect to Logseq API. "
                "Is Logseq running with the HTTP API enabled?",
                as_json=as_json,
                reason="connection_refused",
            )
        except requests.HTTPError as e:
            status = e.response.status_code
            hint = ("Check --token: Logseq rejected it." if status in (401, 403)
                    else None)
            fail(
                f"HTTP {status} - {e.response.text}",
                as_json=as_json,
                reason="http_error",
                status_code=status,
                **({"hint": hint} if hint else {}),
            )
        except DatalogQueryError as e:
            # Not a transport error: the connection is healthy, Logseq rejected
            # the query itself. A distinct reason keeps agents from running
            # doctor (which reports OK) and falling back to the filesystem.
            fail(
                str(e),
                as_json=as_json,
                reason="datalog_query_failed",
                query=e.query,
            )
        except ConfigError as e:
            # Nothing was sent and nothing is wrong with Logseq: a setting that
            # describes the user's graph is missing or their config is broken.
            # Its own reason keeps an agent from retrying or blaming the
            # connection; the message names the setting and the file.
            fail(
                str(e),
                as_json=as_json,
                reason="config_error",
            )
        except InvalidKeywordError as e:
            # The connection is healthy and no query was sent; the input was
            # rejected before building. A distinct reason keeps this out of the
            # "connection down" path an agent would otherwise take.
            fail(
                str(e),
                as_json=as_json,
                reason="invalid_property_key",
            )
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


def _project_pattern(tag_prefix: str, explicit_tags=None) -> "re.Pattern[str]":
    """Match project mentions, as a tag or as a page link.

    ``#projects/alpha`` and ``[[projects/alpha]]`` name the same project, and a
    graph that namespaces project pages tends to contain both, so matching only
    the tag form undercounts. Group 1 is the project name either way.

    ``explicit_tags`` is for graphs that do not namespace at all and tag flatly
    (``#alpha``, ``#beta``): those names cannot be inferred, so they are listed.
    """
    prefix = tag_prefix.lstrip("#")
    alts = [
        r"#" + re.escape(prefix) + r"(\S+)",
        r"\[\[" + re.escape(prefix) + r"([^\]]+)\]\]",
    ]
    for raw in explicit_tags or []:
        name = str(raw).strip().lstrip("#")
        if name:
            alts.append(r"#(" + re.escape(name) + r")\b")
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


_BLOCK_REF_RE = re.compile(r'\(\(([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\)\)')
_TODO_MARKERS = {"TODO", "DOING", "DONE", "LATER", "NOW", "CANCELED", "WAIT", "WAITING"}
# A text replacement must skip property lines: rewriting an id:: line breaks
# every ((block-ref)) to that block, irreversibly. Regex shared via helpers.

# find-block --with-children costs one extra read per match (the datalog pull
# carries no children), so the fan-out is capped and the remainder reported.
FIND_BLOCK_CHILDREN_LIMIT = 25


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

    if not block:
        # The API answers an unknown UUID with null, so returning that verbatim
        # on stdout with exit 0 reads as a successful empty block. Report it the
        # way get-page reports a missing page: non-zero exit, error on stderr.
        fail(f"Block not found: {block_id}", as_json=as_json,
             id=block_id, exists=False)

    if as_json:
        output(block, True)
    else:
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


# ---------------------------------------------------------------------------
# 3b. find-block
# ---------------------------------------------------------------------------
@cli.command("find-block", epilog="""\b
Examples:
  logseq-cli --token TOKEN find-block --content "tag support" --page "Project Alpha" --first
  logseq-cli --token TOKEN find-block --content "^### " --page "X" --regex
  logseq-cli --token TOKEN find-block --content "14:57" --page "2026-07-22, tuesday" --with-children
Note:
  Output gives uuid + page + content preview. Use --first to disambiguate; pipe to
  insert-block --child-of, update-block, remove-block downstream.
  --with-children prints each match with its sub-blocks indented, instead of
  guessing a line count with `get-page | grep -A<n>`.
""")
@click.option("--content", required=True, help="Content text (substring match or regex with --regex)")
@click.option("--page", "--name", default=None, help="Restrict search to this page name")
@click.option("--regex", "use_regex", is_flag=True, help="Interpret --content as regex pattern")
@click.option("--first", "first_only", is_flag=True, help="Output only the first match")
@click.option("--with-children", "with_children", is_flag=True, help="Print each match with its sub-blocks (one extra API read per match)")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
@handle_connection_error
def find_block(ctx, content, page, use_regex, first_only, with_children, as_json):
    """Find blocks by content substring or regex."""
    api = ctx.obj["api"]
    matches = find_blocks_by_content(api, content, page=page, use_regex=use_regex)

    if first_only:
        matches = matches[:1]

    # The datalog pull returns no children, so each subtree costs one extra
    # read. Bounded so a broad --content cannot fan out into hundreds of calls;
    # what was skipped is stated rather than silently dropped.
    truncated = 0
    if with_children and matches:
        if len(matches) > FIND_BLOCK_CHILDREN_LIMIT:
            truncated = len(matches) - FIND_BLOCK_CHILDREN_LIMIT
            matches = matches[:FIND_BLOCK_CHILDREN_LIMIT]
        for block in matches:
            uuid = block.get("uuid")
            if not uuid:
                continue
            full = api.get_block(uuid, include_children=True)
            if full:
                block["children"] = full.get("children") or []

    if as_json:
        output(matches, True)
    else:
        if not matches:
            click.echo("No blocks found.")
        else:
            click.echo(f"Found {len(matches)} block(s):")
            for block in matches:
                uuid = block.get("uuid") or "?"
                page_info = block.get("page")
                page_name = ""
                if isinstance(page_info, dict):
                    page_name = page_info.get("original-name") or page_info.get("name") or ""
                click.echo(f"  uuid: {uuid}")
                if page_name:
                    click.echo(f"  page: {page_name}")
                if with_children:
                    # full content, not a preview: truncating the head of a
                    # subtree would defeat the point of asking for its children
                    click.echo(f"  content: {block.get('content') or ''}")
                    children = block.get("children") or []
                    if children:
                        click.echo(process_blocks(children, indent=2))
                else:
                    preview = (block.get("content") or "")[:80].replace("\n", " ")
                    click.echo(f"  content: {preview}")
                click.echo()
            if truncated:
                click.echo(
                    f"({truncated} further match(es) not expanded; narrow --content "
                    "or --page, or use --first)", err=True)


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
    mood_indicator_patterns = [
        "mood:", "feeling:", "\U0001f60a", "\U0001f614", "\U0001f620", "\U0001f60c",
        "happy", "sad", "angry", "excited", "tired", "anxious",
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
    incomplete_task = re.compile(
        r"(?i:- \[ \])|^\s*-?\s*(?:TODO|DOING|NOW|LATER|WAITING|IN-PROGRESS)\b",
        re.MULTILINE)
    complete_task = re.compile(
        r"(?i:- \[x\])|^\s*-?\s*(?:DONE|CANCELED|CANCELLED)\b",
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


def _property_key_spellings(key: str):
    """Return the datalog spellings to try for a property key.

    Logseq stores property keys kebab-cased in datalog but shows them
    camelCased. A camelCase key gets its kebab form added so either spelling
    the user types finds the page; the camelCase form is kept too, in case a
    foreign graph stored it that way. Order preserved, duplicates dropped.
    """
    kebab = re.sub(r"([A-Z])", lambda m: "-" + m.group(1).lower(), key)
    forms = [key]
    if kebab != key:
        forms.append(kebab)
    return forms


def _find_stored_property_key(props: dict, key: str):
    """Find the stored spelling of a user-typed property key.

    The API returns camelCase keys (excludeFromGraphView), datalog and habit
    spell them kebab-cased; a plain .lower() matches neither. Compare with
    dashes stripped and case folded so every spelling finds the stored key.
    """
    want = key.replace("-", "").lower()
    for stored in props:
        if stored.replace("-", "").lower() == want:
            return stored
    return None


def _read_property_value(props: dict, key: str):
    """Read a property value trying every spelling of the key.

    The datalog pull returns kebab-cased keys, so a user who typed the
    camelCase form would otherwise read an empty value off a page the query
    did find. Try each spelling, first hit wins.
    """
    for form in _property_key_spellings(key):
        if form in props:
            return props[form]
    return ""


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

    # --advanced mode: pass raw Datalog query directly to datascript_query.
    # This is the one place that does NOT go through the datalog build layer,
    # and that is correct: --advanced is the documented raw pass-through for
    # arbitrary Datalog. Do not "fix" it to route through edn_string.
    if advanced:
        query_str = request
        description = "Advanced (raw Datalog query)"
        # A rejected query raises DatalogQueryError, caught by the decorator: a
        # query that never ran must exit non-zero, not report an empty result.
        results = api.datascript_query(query_str)

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
                ' :where [?b :block/refs ?target] [?target :block/name {page_name}]]'
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
                ' :where [?b :block/content ?c] [(clojure.string/includes? ?c {tag_name})]]'
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
            # Built from config: which property marks a person page is the
            # user's own convention, not something the CLI can know.
            "query": None,
            "needs_config": ("graph", "person_property"),
            "description": "All person pages",
        },
        "projects": {
            "keywords": ["project", "projects", "projekte"],
            # Built from config: the namespace that marks project pages differs
            # per graph, so there is no default to fall back on.
            "query": None,
            "needs_config": ("graph", "projects_namespace"),
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
            f' [(clojure.string/includes? ?c {edn_string(search_term)})]]'
        )
        # A real error (connection down, rejected query) must surface via the
        # decorator, not be turned into a page-name search: that would answer a
        # different question with exit 0. The fallback is for the fachliche
        # case only, no content hits, so it keys off an empty result.
        results = api.datascript_query(content_query)
        if results:
            query_used = content_query
            description = f"Content search for '{search_term}'"
        else:
            # No content hits: try page names as a last resort.
            pages = api.get_all_pages()
            results = [
                p for p in pages
                if req_lower in (p.get("name") or "").lower()
            ]
            query_used = f"(page name search for '{request}')"
            description = "Page name search (no content match)"
    else:
        template = query_templates[best_match]
        query_str = template["query"]

        # Templates that describe the user's own graph carry no query of their
        # own: it is built here from config. A missing setting raises and exits
        # non-zero rather than querying for a guessed namespace, which would
        # return an empty list that looks exactly like "no projects".
        if query_str is None:
            section, key = template["needs_config"]
            cfg = load_config()
            if key == "projects_namespace":
                prefix = require(cfg, section, key,
                                 f"smart-query --request {request!r}")
                query_str = (
                    '[:find (pull ?p [*]) :where [?p :block/name ?n] '
                    f'[(clojure.string/starts-with? ?n {edn_string(str(prefix).lower())})]]'
                )
            else:
                prop = require(cfg, section, key,
                               f"smart-query --request {request!r}")
                value = require(cfg, section, "person_value",
                                f"smart-query --request {request!r}")
                query_str = (
                    '[:find (pull ?p [*]) :where [?p :block/name] '
                    '[?p :block/properties ?props] '
                    f'[(get ?props :{edn_keyword(str(prop))}) ?t] '
                    f'[(= ?t {edn_string(str(value))})]]'
                )

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
            if not param_value:
                param_value = request.strip()
            # Each template's placeholder needs the build function that matches
            # its query position, and the two known ones differ on purpose:
            # links-to queries :block/name (stored lowercased), tagged queries
            # :block/content (user spelling). Sending links-to through
            # edn_string would keep the case bug that lost 1569 backlinks.
            if best_match == "links-to":
                literal = page_name_literal(param_value)
            elif best_match == "tagged":
                literal = edn_string("#" + param_value)
            else:
                literal = edn_string(param_value)
            query_str = query_str.replace(f"{{{extract_param}}}", literal)
            description = template["description"].replace(f"{{{extract_param}}}", param_value)
        else:
            description = template["description"]

        # A rejected query raises DatalogQueryError (caught by the decorator):
        # a query that never ran must fail loud, not report zero hits.
        results = api.datascript_query(query_str)
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
@click.option("--dry-run", is_flag=True, help="Show what would be inserted (block count + position) without writing")
@click.option("--quiet", is_flag=True, help="With --tree: print only the confirmation line, not one uuid line per block")
@click.option("--json", "as_json", is_flag=True, help="JSON output")
@click.pass_context
@handle_connection_error
def insert_block_cmd(ctx, page, after, before, child_of, as_first, top_level, content, tree_input, tree_file, properties, dry_run, quiet, as_json):
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

        # Resolve target + position first (no writes), so --dry-run can report
        # the plan and bail before touching the graph.
        if child_of:
            clean_id = child_of.strip().replace("((", "").replace("))", "")
            position = f"{'first child' if as_first else 'child'} of {clean_id[:8]}..."
            if as_first:
                do_insert = lambda: insert_block_tree_as_first_children(api, tree, clean_id)
            else:
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

    markers_str = " ".join(edn_string(m) for m in sorted(markers))
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
@click.option("--dry-run", "dry_run", is_flag=True, help="Show the marker change, without writing")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
@handle_connection_error
def set_todo_status(ctx, block_id, content, page, status, follow_refs, dry_run, as_json):
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
        matches = find_blocks_by_content(api, content, page=page)
        # Prefer blocks that actually carry a TODO marker: a status change is
        # only meaningful there, and it disambiguates a text that also appears
        # in prose.
        todo_matches = [m for m in matches if m.get("content", "").split()[0:1] and
                        m.get("content", "").split()[0].upper() in _TODO_MARKERS]
        candidates = todo_matches or matches
        if not candidates:
            click.echo(f"No block found matching '{content}' on page '{page}'.", err=True)
            sys.exit(1)
        if len(candidates) > 1:
            # Taking the first match would silently rewrite one of several
            # equally valid blocks, and the caller could not tell which. This
            # command overwrites content, so an ambiguous selector must stop.
            listing = "\n".join(
                f"  {m.get('uuid')}  {(m.get('content') or '')[:70]}"
                for m in candidates[:10]
            )
            more = f"\n  ... and {len(candidates) - 10} more" if len(candidates) > 10 else ""
            click.echo(
                f"{len(candidates)} blocks match '{content}' on page '{page}'; refusing "
                f"to guess which one to update. Narrow --content or pass --id:\n"
                f"{listing}{more}", err=True)
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

    # The marker swap is the whole change, so the preview shows both markers and
    # the line they sit on — enough to tell the right block from a near-identical
    # one before committing. Resolution and the ambiguity guard above already ran.
    old_marker = old_content.split()[0] if old_content.split() else ""
    if old_marker.upper() not in _TODO_MARKERS:
        old_marker = ""

    if dry_run:
        if as_json:
            output({"uuid": block_id, "old_marker": old_marker, "new_marker": status,
                    "old": old_content, "new": new_content, "status": status,
                    "dry_run": True}, True)
        else:
            click.echo(f"[DRY RUN] Would set status on block {block_id}")
            click.echo(f"  marker: {old_marker or '(none)'} -> {status}")
            preview = old_content[:60] + ("..." if len(old_content) > 60 else "")
            click.echo(f"  was: {preview}")
            preview = new_content[:60] + ("..." if len(new_content) > 60 else "")
            click.echo(f"  now: {preview}")
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
  logseq-cli --token TOKEN get-properties --name "Alice"
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
        stored_key = _find_stored_property_key(properties, prop_name)
        if stored_key is None:
            fail(f"Property '{prop_name}' not found on '{page_name}'.",
                 as_json=as_json, page=page_name, property=prop_name)
        value = properties.get(stored_key)
        text_key = _find_stored_property_key(text_values, prop_name)
        text_value = text_values.get(text_key) if text_key else None

        if as_json:
            output({"page": page_name, "property": stored_key, "value": value, "text": text_value}, True)
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
@click.option("--dry-run", "dry_run", is_flag=True, help="Show the property change, without writing")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
@handle_connection_error
def set_property(ctx, page, key, value, dry_run, as_json):
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

    if dry_run:
        # Whether this creates or overwrites is the fact worth previewing: the
        # command is called "set" either way, and an unnoticed overwrite loses
        # the old value with no trace. It is read off the block already fetched.
        existing = first_block.get("properties") or {}
        had = key in existing
        old_value = existing.get(key)
        if as_json:
            output({"page": page, "property": key, "old_value": old_value,
                    "value": value, "existed": had, "dry_run": True}, True)
        else:
            click.echo(f"[DRY RUN] Would set '{key}::' on page '{page}'")
            if had:
                click.echo(f"  was: {old_value}")
            else:
                click.echo(f"  was: (not set)")
            click.echo(f"  now: {value}")
        return

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
Examples:
  logseq-cli --token TOKEN remove-property --name "X" --key "deprecated_key"
  logseq-cli --token TOKEN remove-property --id UUID --key "prio"
Note:
  --name removes a PAGE property (stored on the page's first block).
  --id removes the property from that one block, wherever it sits.
""")
@click.option("--page", "--name", default=None, help="Page name (removes a page property)")
@click.option("--id", "block_id", default=None, help="Block UUID (removes the property from that block)")
@click.option("--key", required=True, help="Property key to remove")
@click.option("--dry-run", "dry_run", is_flag=True, help="Show which property would be removed, without writing")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
@handle_connection_error
def remove_property(ctx, page, block_id, key, dry_run, as_json):
    """Remove a property from a page or from a single block."""
    api = ctx.obj["api"]
    if bool(page) == bool(block_id):
        fail("Specify exactly one of: --name, --id.", as_json=as_json)

    if block_id:
        # A page property is just a property on the page's first block, so the
        # API call is the same; only the way the block is found differs.
        block_uuid = block_id.strip().replace("((", "").replace("))", "")
        block = api.get_block(block_uuid, include_children=False)
        if not block:
            fail(f"Block not found: {block_uuid}", as_json=as_json, id=block_uuid)
        existing = (block.get("properties") if isinstance(block, dict) else None) or {}
        target = f"block '{block_uuid}'"
        result = {"id": block_uuid, "property": key, "status": "removed"}
    else:
        blocks = api.get_page_blocks_tree(page)
        if not blocks:
            fail(f"Page '{page}' not found or has no blocks", as_json=as_json, page=page)
        block_uuid = blocks[0].get("uuid")
        if not block_uuid:
            fail("Could not find block UUID", as_json=as_json, page=page)
        existing = blocks[0].get("properties") or {}
        target = f"page '{page}'"
        result = {"page": page, "property": key, "status": "removed"}

    if dry_run:
        # "Property not there" is the outcome worth knowing before the write:
        # the real call succeeds silently either way, so a caller who misspelled
        # the key would otherwise see "Removed" and believe it.
        present = key in existing
        if as_json:
            output({**result, "status": "would_remove" if present else "not_present",
                    "value": existing.get(key), "present": present,
                    "dry_run": True}, True)
        elif present:
            click.echo(f"[DRY RUN] Would remove '{key}' from {target}")
            click.echo(f"  value: {existing[key]}")
        else:
            click.echo(f"[DRY RUN] '{key}' is not set on {target}; nothing would be removed")
        return

    api.remove_block_property(str(block_uuid), key)

    if as_json:
        output(result, True)
    else:
        click.echo(f"Removed '{key}' from {target}")


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
@click.option("--dry-run", "dry_run", is_flag=True, help="Show the property change, without writing")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
@handle_connection_error
def set_block_property(ctx, block_id, key, value, dry_run, as_json):
    """Set or update a property on a specific block."""
    api = ctx.obj["api"]

    # Auto-detect value type (shared coercion with the inline --property option)
    value = coerce_property_value(value)

    if dry_run:
        # The write path sets the property blind — upsert needs no prior read.
        # The preview does need one: without it there is no old value to show,
        # and it also turns a mistyped UUID into an error instead of a silent
        # no-op. One extra read, only on this path.
        block = api.get_block(block_id, include_children=False)
        if not block:
            fail(f"Block not found: {block_id}", as_json=as_json, id=block_id)
        existing = (block.get("properties") if isinstance(block, dict) else None) or {}
        had = key in existing
        old_value = existing.get(key)
        if as_json:
            output({"block": block_id, "property": key, "old_value": old_value,
                    "value": value, "existed": had, "dry_run": True}, True)
        else:
            click.echo(f"[DRY RUN] Would set '{key}::' on block '{block_id}'")
            click.echo(f"  was: {old_value if had else '(not set)'}")
            click.echo(f"  now: {value}")
        return

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
            referencing = _extract_backlink_names(refs) if refs else []
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

    # Property keys have two spellings for the same data: Logseq displays
    # camelCase (excludeFromGraphView), datalog stores kebab-case
    # (exclude-from-graph-view). Querying the user's spelling as-is finds
    # nothing when they typed the displayed form. Try both, so either works;
    # a foreign graph might store either. Both are whitelisted before use.
    key_forms = _property_key_spellings(key)
    key_get = " ".join(
        f"[(get ?props :{edn_keyword(k)}) ?v]" for k in key_forms
    )
    key_clause = key_get if len(key_forms) == 1 else f"(or {key_get})"
    if value:
        # Query pages where property key matches value
        query = f'''[:find (pull ?p [:block/name :block/original-name :block/properties])
                     :where
                     [?p :block/name]
                     [?p :block/properties ?props]
                     {key_clause}
                     [(= ?v {edn_string(value)})]]'''
    else:
        # Query pages that have this property key (any value)
        query = f'''[:find (pull ?p [:block/name :block/original-name :block/properties])
                     :where
                     [?p :block/name]
                     [?p :block/properties ?props]
                     {key_clause}]'''

    # The former full-scan fallback is gone: it existed for a malformed key,
    # which edn_keyword now rejects before any query is built, and a silent
    # scan over ~1900 pages is no good answer even on success. A rejected key
    # is a usage error with a clear message, not a reason to fall back.
    results = api.datascript_query(query)

    # Extract page names from results
    pages_found = []
    for item in results:
        if isinstance(item, list) and len(item) > 0:
            page = item[0]
            if isinstance(page, dict):
                name = page.get("original-name") or page.get("name", "?")
                prop_value = _read_property_value(page.get("properties", {}), key)
                pages_found.append({"name": name, "value": str(prop_value)})
        elif isinstance(item, dict):
            name = item.get("original-name") or item.get("name", "?")
            prop_value = _read_property_value(item.get("properties", {}), key)
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


@cli.command("init", epilog="""\b
Examples:
  logseq-cli --token TOKEN init --dry-run
  logseq-cli --token TOKEN init
  logseq-cli --token TOKEN init --output ./config.toml --force
Note:
  Reads the graph, never writes to it. Suggestions are counted, not guessed:
  each one comes with how many of the recent journals actually use it, so a
  section you abandoned years ago does not end up in your config.
""")
@click.option("--output", "out_path", default=None,
              help="Where to write (default: the first config search path)")
@click.option("--days", default=120, show_default=True,
              help="How many of the most recent journals to look at")
@click.option("--force", is_flag=True, help="Overwrite an existing config file")
@click.option("--dry-run", "dry_run", is_flag=True, help="Print what would be written")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
@handle_connection_error
def init_config(ctx, out_path, days, force, dry_run, as_json):
    """Suggest a config file from what your graph actually contains."""
    api = ctx.obj["api"]

    target = Path(out_path).expanduser() if out_path else config_search_paths()[0]
    if target.exists() and not (force or dry_run):
        fail(f"{target} already exists. Pass --force to overwrite it, "
             "or --dry-run to see what would be written.",
             as_json=as_json, reason="config_exists")

    pages = api.get_all_pages() or []
    journals = [p for p in pages
                if p.get("journalDay") or p.get("journal-day") or p.get("journal?")]
    # Most recent first: a section abandoned years ago must not outvote the one
    # in use now, which counting the whole history would let it do.
    journals.sort(key=lambda p: p.get("journalDay") or p.get("journal-day") or 0,
                  reverse=True)
    journals = journals[:days]

    heading_counts = Counter()
    for page in journals:
        name = page.get("originalName") or page.get("original-name") or page.get("name")
        if not name:
            continue
        seen = set()
        for block in _walk_blocks(api.get_page_blocks_tree(name) or []):
            text = (block.get("content") or "").strip()
            if text.startswith("#"):
                seen.add(normalize_heading(text))
        heading_counts.update(seen)

    namespaces = Counter()
    prop_values = Counter()
    for page in pages:
        name = (page.get("originalName") or page.get("original-name")
                or page.get("name") or "")
        if "/" in name:
            namespaces[name.split("/", 1)[0] + "/"] += 1
        props = page.get("properties") or {}
        if isinstance(props, dict):
            for value in _as_list(props.get("type")):
                prop_values[str(value)] += 1

    total = len(journals)
    suggestions = {
        "journals_examined": total,
        "headings": heading_counts.most_common(8),
        "namespaces": namespaces.most_common(5),
        "person_values": prop_values.most_common(5),
    }
    toml_text = _render_config(heading_counts, namespaces, prop_values, total)

    if as_json:
        output({"target": str(target), "written": False if dry_run else None,
                "suggestions": suggestions, "config": toml_text}, True)
        if dry_run:
            return
    else:
        click.echo(f"Looked at {total} journal page(s).")
        if not total:
            click.echo("  No journals found — is the right graph open?")
        for heading, count in heading_counts.most_common(8):
            click.echo(f"  {count:4}/{total}  {heading}")
        for ns, count in namespaces.most_common(5):
            click.echo(f"  {count:4} pages under  {ns}")
        for value, count in prop_values.most_common(5):
            click.echo(f"  {count:4} pages with   type:: {value}")
        click.echo()

    if dry_run:
        if not as_json:
            click.echo(f"[DRY RUN] Would write {target}:\n")
            click.echo(toml_text)
        return

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(toml_text, encoding="utf-8")
    if not as_json:
        click.echo(f"Wrote {target}")
        click.echo("Review it: these are counts from your graph, not certainties.")


def _walk_blocks(blocks):
    """Yield every block in a tree, depth first."""
    for block in blocks:
        yield block
        yield from _walk_blocks(block.get("children") or [])


def _as_list(value):
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _slug(heading: str) -> str:
    """A short name for a heading, usable as a TOML key."""
    text = re.sub(r"^#+\s*", "", heading)
    text = re.sub(r"\[\[([^\]]*)\]\]", r"\1", text)
    text = re.sub(r"[^0-9A-Za-z]+", "_", text).strip("_").lower()
    return text or "section"


def _tie_note(counts, what: str) -> list:
    """Name the runners-up when the count cannot separate them.

    `Counter.most_common` breaks a tie by insertion order, so whichever page
    the API happened to return first would decide — and the comment written
    next to the winner ("10 pages live under this prefix") reads as evidence
    while hiding that something else scored exactly the same. In the graph
    this was found in, two namespaces had ten pages each and the wrong one
    was picked, after which `smart-query` returned ten confident non-results.

    Returns comment lines, or nothing when there is a clear winner.
    """
    ranked = counts.most_common()
    if not ranked:
        return []
    top_count = ranked[0][1]
    rivals = [name for name, count in ranked[1:] if count == top_count]
    if not rivals:
        return []
    return [f"# just as common, and possibly the {what} you want: "
            + ", ".join(str(r) for r in rivals),
            "# counting cannot tell them apart — pick the right one yourself"]


def _render_config(headings, namespaces, prop_values, total) -> str:
    """Build the config text, commenting out anything that is a guess."""
    lines = [
        "# Written by `logseq-cli init` from the graph it found.",
        "# The counts say how many of the recent journals use each heading;",
        "# check them, they are evidence rather than certainty.",
        "",
        "[journal]",
    ]
    ranked = headings.most_common(8)
    # Several sections can appear in every journal, and then the count alone
    # does not say which one prose goes under. Prefer a plain top-level
    # heading: one that is not a link to a page ("## [[Meeting]]" collects
    # meetings) and not a sub-heading, which is where notes usually live.
    def _is_plain_top_level(h: str) -> bool:
        return h.startswith("## ") and not h.startswith("### ") and "[[" not in h

    default_pick = next(
        ((h, c) for h, c in ranked if _is_plain_top_level(h)),
        ranked[0] if ranked else None,
    )
    if default_pick:
        top, count = default_pick
        lines.append(f'# in {count} of {total} journals')
        lines += _tie_note(
            Counter({h: c for h, c in ranked if _is_plain_top_level(h)}),
            "section")
        lines.append(f'default_heading = "{top}"')
    else:
        lines.append('# No headings found; journal writes go in at top level.')
        lines.append('# default_heading = "## Log"')

    lines += ["", "[journal.headings]",
              "# The key is yours to choose; the value must match the graph exactly."]
    used = set()
    for heading, count in ranked:
        key = _slug(heading)
        while key in used:
            key += "_"
        used.add(key)
        lines.append(f'{key} = "{heading}"  # {count}/{total}')

    lines += ["", "[graph]"]
    if namespaces:
        ns, count = namespaces.most_common(1)[0]
        lines.append(f"# {count} pages live under this prefix")
        lines += _tie_note(namespaces, "namespace")
        lines.append(f'projects_namespace = "{ns}"')
    else:
        lines.append("# No namespaced pages found. Without this setting,")
        lines.append('# `smart-query --request "projects"` reports it as missing.')
        lines.append('# projects_namespace = "projects/"')

    if prop_values:
        value, count = prop_values.most_common(1)[0]
        lines.append(f"# {count} pages carry type:: {value}")
        lines += _tie_note(prop_values, "type:: value")
        lines.append('person_property = "type"')
        lines.append(f'person_value = "{value}"')
    else:
        lines.append("# No type:: properties found.")
        lines.append('# person_property = "type"')
        lines.append('# person_value = "Person"')

    lines += [
        "",
        "# [analysis] is not guessed: which words carry mood in your journal is",
        "# not something a count can tell. The defaults are English; see",
        "# docs/configuration.md and config.example.toml.",
        "",
    ]
    return "\n".join(lines)


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

    # 0. The runtime itself. Everything below assumes the CLI is installed
    #    correctly; when it is not, the failure surfaces later as something
    #    unrelated (an ImportError mid-command, a config that never loads).
    py = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    py_ok = sys.version_info >= (3, 10)
    add("python", py_ok,
        py + ("" if py_ok else "  (3.10 or newer required)"))

    missing = []
    versions = []
    for mod, label in (("click", "click"), ("requests", "requests")):
        try:
            versions.append(f"{label} {import_module(mod).__version__}")
        except Exception:  # noqa: BLE001 - any import failure means "not usable"
            missing.append(label)
    # The TOML parser is stdlib from 3.11 and the tomli backport before that;
    # either is fine, only having neither is a problem, and only for configs.
    try:
        import_module("tomllib")
        versions.append("tomllib (stdlib)")
    except ModuleNotFoundError:
        try:
            versions.append(f"tomli {import_module('tomli').__version__}")
        except Exception:  # noqa: BLE001
            missing.append("tomli (needed on Python 3.10 to read a config file)")
    add("packages", not missing,
        ", ".join(versions) if not missing else "missing: " + ", ".join(missing))
    if missing:
        remedy = remedy or 'Reinstall the package: pip install -e ".[dev]"'

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

    # Config last: it says nothing about whether Logseq is reachable, so it is
    # reported with ok=None and cannot turn a working setup into a failed one.
    # Without it most commands are fine; the point is to name the few that are
    # not, before the user hits one and wonders why it found nothing.
    try:
        cfg = load_config()
        configured = [
            key for section, key in (
                ("graph", "projects_namespace"),
                ("graph", "person_property"),
            )
            if cfg.get(section, {}).get(key)
        ]
        if not cfg:
            add("config", None,
                "no config file; commands that need one will say so "
                "(see docs/configuration.md)")
        elif configured:
            add("config", True, f"{cfg['_path']} ({', '.join(configured)})")
        else:
            add("config", None,
                f"{cfg['_path']} carries no [graph] settings; "
                "smart-query for projects or people will report them missing")
    except ConfigError as e:
        # A broken config is worth failing on: the user meant to configure
        # something and it is not being applied.
        add("config", False, str(e).split("\n")[0])
        remedy = remedy or "Fix the config file, or remove it to run without one."

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
