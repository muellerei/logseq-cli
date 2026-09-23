import json
import sys

import click
import requests

from logseq_cli.blocktext import refuse_split_block, refuse_split_heading, refuse_split_tree
from logseq_cli.group import cli
from logseq_cli.helpers import (
    BlockIdError,
    apply_block_properties,
    check_block_ids,
    check_property_pairs,
    content_or_file,
    count_blocks,
    extract_page_links,
    find_backlinks,
    find_heading,
    find_or_create_heading,
    incoming_block_refs,
    insert_block_tree_with_uuids,
    insert_tree_at_page_end,
    is_journal_date,
    note_quote_breaks,
    parse_hierarchical_content,
    parse_property_pairs,
    process_blocks,
    refs_refusal,
    require_insert,
    strip_title_heading,
    tree_without_block_ids,
    uuid_fields,
)
from logseq_cli.output import fail, handle_connection_error, json_text, output
from logseq_cli.render import (
    blocks_to_markdown,
    blocks_with_ids,
    bound,
    count_unresolved_refs,
    extract_backlink_names,
    extract_section,
    first_uuids,
    is_properties_block,
    outline_blocks,
    resolve_refs_in_blocks,
)


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

@cli.command("get-page", epilog="""\b
Examples:
  logseq-cli --token TOKEN get-page --name "Project Alpha"
  logseq-cli --token TOKEN get-page --name "2026-05-08, friday" --resolve-refs --with-ids
  logseq-cli --token TOKEN get-page --name "Project Alpha" --heading "## Open Points"
  logseq-cli --token TOKEN get-page --name "Project Alpha" --outline
  logseq-cli --token TOKEN get-page --name "Project Alpha" --max-chars 20000
  logseq-cli --token TOKEN get-page --name A --name B    # batch read
Notes:
  --resolve-refs inlines ((uuid)) block-refs (saves N×get-block).
  --with-ids prefixes each line with the block UUID (replaces --json | jq).
  --heading returns only the matching heading-block + its children.
  --outline lists the headings with their UUIDs, the page's table of contents;
  with --heading, the outline of that section.
  --max-chars cuts the blocks so the output fits, in the format printed. The
  cut falls between blocks; pages past it are not printed. stderr names what
  was withheld and where; --json carries the same as "withheld" and "cut".
  --from-block continues a cut read: same command, plus the uuid the note
  names. The block's ancestors come along as context. A block that does not
  fit is named with the --max-chars it needs instead. Both flags refuse a
  page named twice.
""")
@click.option("--page", "--name", required=True, multiple=True, help="Page name (repeatable for batch: --name A --name B)")
@click.option("--no-backlinks", is_flag=True, help="Skip backlink computation")
@click.option("--resolve-refs", is_flag=True, help="Inline ((uuid)) block references with their content")
@click.option("--with-ids", "with_ids", is_flag=True, help="Prefix each block line with its UUID (format: <uuid>\\t<indent>\\t<content>)")
@click.option("--heading", default=None, help="Return only the section under this heading (e.g. '## Focus Topics W17'). Searches recursively.")
@click.option("--outline", is_flag=True, help="Only the headings, one line each with its UUID, indented by how they nest: a heading inside another's section one tab deeper. A heading is what Logseq reads as one. No backlinks; not with --format markdown")
@click.option("--max-chars", "max_chars", type=int, default=None, help="Cut the blocks so the output fits in N characters, 1 or greater. The cut falls between blocks; what is withheld is reported on stderr and, with --json, as 'withheld'/'cut' fields. Page headers and backlinks are not cut")
@click.option("--from-block", "from_block", default=None, help="Start at this block, as named by a --max-chars note: pages and blocks before it are skipped, its ancestors kept as context")
@click.option("--format", "output_format", type=click.Choice(["text", "markdown"]), default="text", help="Output format: text (default) or markdown (Logseq-compatible)")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
@handle_connection_error
def get_page(ctx, page, no_backlinks, resolve_refs, with_ids, heading, outline, max_chars, from_block, output_format, as_json):
    """Get page content with backlinks. Pass --name multiple times for batch reads."""
    api = ctx.obj["api"]
    missing = []
    dead_refs = []

    # Before any read: a refused call should cost nothing and say why.
    if max_chars is not None and max_chars < 1:
        fail("--max-chars must be 1 or greater.", as_json)
    if outline and output_format == "markdown" and not as_json:
        # Markdown has nowhere to put the uuid, and the uuid is what an
        # outline is read for.
        fail("--outline prints UUIDs; it cannot be combined with --format markdown.", as_json)
    if max_chars is not None or from_block:
        # A cut read continues by block uuid, and a page read twice holds each
        # uuid twice: the continuation would jump back into the first copy.
        # Logseq finds a page by its lower-cased name (JavaScript
        # toLowerCase, measured on 0.10.15: 'ß' stays apart from 'ss', so
        # casefold() would merge two pages).
        seen = {}
        for name in page:
            if name.lower() in seen:
                fail(f"page '{name}' is named twice ('{seen[name.lower()]}'); "
                     f"--max-chars and --from-block need each page once.", as_json)
            seen[name.lower()] = name

    def _fetch_one(page_name):
        # A page that does not exist is an error, not an empty result: Logseq's
        # getPage returns null for it but a real object for an existing-but-empty
        # page. Without this check both render as "(empty page)" and the caller
        # cannot tell "typo in the name" from "nothing written yet".
        if api.get_page(page_name) is None:
            missing.append(page_name)
        blocks = api.get_page_blocks_tree(page_name)
        if no_backlinks or heading or outline:
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
        if outline and blocks:
            # Before resolving: refs in the blocks the outline drops would
            # cost a lookup each and never be printed.
            blocks = outline_blocks(blocks)
        if resolve_refs and blocks:
            resolve_refs_in_blocks(api, blocks, dead_refs)
        return {"page": page_name, "blocks": blocks, "backlinks": backlinks}

    results = [_fetch_one(p) for p in page]
    first_uuid = first_uuids(results)

    for result in results:
        if result["page"] in missing:
            result["exists"] = False

    def _dead_in(result):
        return [u for u in dead_refs
                if f"(({u}))" in json.dumps(result.get("blocks") or [])]

    def _render(results):
        if as_json:
            annotated = []
            for result in results:
                in_this = _dead_in(result)
                annotated.append({**result, "dead_refs": in_this} if in_this else result)
            # The shape follows what was asked for, not what survived a cut
            # or --from-block: several names answer with a list, always.
            payload = annotated if len(page) > 1 else annotated[0]
            return json_text(payload) + "\n"
        out = []
        for result in results:
            p, blocks, backlinks = result["page"], result["blocks"], result["backlinks"]
            if p in missing:
                placeholder = "(page does not exist)"
            elif result.get("withheld"):
                placeholder = f"({result['withheld']} block(s) withheld by --max-chars)"
            elif outline:
                placeholder = "(no headings)"
            else:
                placeholder = "(empty page)"
            if outline or with_ids:
                out.append(f"=== {p} ===\n\n")
                body = blocks_with_ids(blocks, first_line=outline) if blocks else placeholder
                out.append(body + "\n")
            elif output_format == "markdown":
                starts = bool(blocks) and blocks[0].get("uuid") == first_uuid.get(p)
                out.append((blocks_to_markdown(blocks, page_start=starts)
                            if blocks else placeholder) + "\n")
            else:
                out.append(f"=== {p} ===\n\n")
                out.append((process_blocks(blocks) if blocks else placeholder) + "\n")
                if backlinks:
                    out.append(f"\nBacklinks ({len(backlinks)}):\n")
                    for bl in backlinks:
                        out.append(f"  <- {bl}\n")
            if len(page) > 1:
                out.append("\n")
        return "".join(out)

    try:
        results, note = bound(results, _render, max_chars, from_block, "page")
    except LookupError as exc:
        fail(str(exc), as_json)
    if note:
        click.echo(note, err=True)

    # The notices below speak about what is printed, so they come after the cap.
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
            results_with = [r["page"] for r in results if uuid in _dead_in(r)]
            if not results_with:
                continue
            click.echo(f"⚠️  block-ref (({uuid})) points at a block that no "
                       f"longer exists (on {', '.join(results_with)})", err=True)

    click.echo(_render(results), nl=False)

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

@cli.command("create-page", epilog="""\b
Example:
  logseq-cli --token TOKEN create-page --name "Alice Example"
Note:
  For pages with properties, use create-page (no --content) + multiple set-property,
  THEN add-note-content for the body. Properties via --content land as bullet-blocks
  (NOT as real properties).
  --content is ONE block: a "- " or "# " line after the first, or a code fence
  nothing closes, is refused, since Logseq would read it as a block of its own.
  A quote ends at a blank line, and the paragraph after it shows as plain
  text: written anyway, with a Note on stderr. Start the blank line with ">"
  to keep the paragraph in the quote.
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
    if content:
        # Written as one block, so it must come back as one (#47).
        refuse_split_block(content, command="create-page")

    # Only for a write that would happen: a live run on a page that exists is
    # refused below, and a note in front of that error would break its JSON;
    # a preview of that run says it would not write.
    if content and not exists:
        note_quote_breaks([{"content": content}])
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

@cli.command("add-note-content", epilog="""\b
Examples:
  logseq-cli --token TOKEN add-note-content --page "Alice" --content "Body text"
  logseq-cli --token TOKEN add-note-content --page "Project Alpha" \\
    --under-heading "## Roadmap" --content "Phase 2 - Kickoff"
Note:
  Counterpart of add-journal-block --under-heading for non-journal pages.
  Heading is created if missing.
  id:: lines are dropped unless --keep-ids is given, and the command says so.
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
@click.option("--page", "--name", required=True, help="Page name")
@click.option("--content", default=None, help="Content to add; this or --content-file is required")
@click.option("--content-file", "content_file", default=None, help="Read --content from this file instead ('-' reads stdin), so apostrophes, quotes and umlauts need no shell quoting. Mutually exclusive with --content")
@click.option("--create/--no-create", default=True, help="Create page if it doesn't exist")
@click.option("--under-heading", default=None, help="Insert content under this heading; create heading if missing")
@click.option("--property", "properties", multiple=True, help="Set KEY=VALUE property on the created (root) block; repeatable. KEY follows set-property's rule: lower-cased, '_' read as '-', refused if Logseq would drop it")
@click.option("--dry-run", "dry_run", is_flag=True, help="Show target page, heading and block count, without writing")
@click.option("--keep-ids", "keep_ids", is_flag=True, help="Keep the id:: values in the content instead of letting Logseq mint new ones, for moving or restoring an outline; an id only a ((ref)) still holds is restored, so the ref resolves again. Refused before any write: an id a block or page still has, a repeated or malformed one, two in one block")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
@handle_connection_error
def add_note_content(ctx, page, content, content_file, create, under_heading, properties, dry_run, keep_ids, as_json):
    """Add content to any page."""
    api = ctx.obj["api"]
    content = content_or_file(content, content_file)

    # Validate property pairs up-front so a bad pair fails before any write.
    try:
        check_property_pairs(properties)
    except ValueError as e:
        fail(str(e), as_json=as_json)
    refuse_split_heading(under_heading, command="add-note-content")

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

    # Checked before the dry run returns: a refused id is part of the preview.
    # The tree checked is the tree written; parsing the text again after
    # removing lines from it is how an announced id line once stayed in.
    tree = parse_hierarchical_content(content)
    refuse_split_tree(tree, command="add-note-content")
    try:
        note = check_block_ids(api, tree, keep_ids)
    except BlockIdError as e:
        fail(str(e), as_json=as_json, **{e.field: e.ids})
    if note:
        click.echo(note, err=True)
        tree = tree_without_block_ids(tree)

    if dry_run:
        # Everything below this point writes — the page, possibly the heading,
        # then the blocks. The block count comes from the same parse the live
        # path uses, so the preview reports what would actually land, not the
        # raw line count.
        planned = count_blocks(tree)
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
        uuids = insert_block_tree_with_uuids(api, tree, heading_uuid, keep_ids=keep_ids)
        position = f"under '{under_heading}' on '{page}'"
    else:
        uuids = insert_tree_at_page_end(api, page, tree, keep_ids=keep_ids)
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

@cli.command("delete-page", epilog="""\b
Examples:
  logseq-cli --token TOKEN delete-page --name "Obsolete Page" --dry-run
  logseq-cli --token TOKEN delete-page --name "Obsolete Page" --force
Note:
  Destructive. Interactively (TTY) it prompts; non-interactively it REQUIRES
  --force and fails otherwise — --json alone is not a confirmation.
  Refuses while ((block-refs)) from other pages point into it, and lists
  them; --force does not override that, --ignore-refs does.
""")
@click.option("--page", "--name", required=True, help="Page name to delete")
@click.option("--force", is_flag=True, help="Skip confirmation prompt (required when non-interactive)")
@click.option("--ignore-refs", is_flag=True, help="Delete even though ((block-refs)) from other pages point into it")
@click.option("--dry-run", is_flag=True, help="Show what would be deleted, without deleting")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
@handle_connection_error
def delete_page(ctx, page, force, ignore_refs, dry_run, as_json):
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

    # Asked by page, not by the uuids read above: that read may have failed,
    # and --force proceeds without it. By the name Logseq resolved, not the
    # one typed; see incoming_block_refs.
    refs = incoming_block_refs(api, page=page_data.get("name") or page)
    if refs and not ignore_refs:
        fail(refs_refusal(refs, f"page '{page}' from other pages"),
             as_json=as_json, page=page, refs=refs)

    if dry_run:
        if as_json:
            output({"page": page, "blocks": block_count, "refs_broken": len(refs),
                    "dry_run": True}, True)
        else:
            click.echo(f"[DRY RUN] Would delete page '{page}' ({block_count} block(s))")
            if refs:
                click.echo(f"  incoming block refs that would dangle: {len(refs)}")
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
    result = {"page": page, "status": "deleted", "blocks": block_count,
              "refs_broken": len(refs)}
    if as_json:
        output(result, True)
    else:
        size = "unknown" if block_count is None else f"{block_count} block(s)"
        click.echo(f"Deleted page '{page}' ({size})")
        if refs:
            click.echo(f"  {len(refs)} incoming block ref(s) now point at nothing")

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
