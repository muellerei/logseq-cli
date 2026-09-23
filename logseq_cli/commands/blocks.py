import datetime

import click

from logseq_cli.group import cli
from logseq_cli.helpers import find_blocks_by_content, process_blocks
from logseq_cli.output import fail, follow_page, handle_connection_error, output


FIND_BLOCK_CHILDREN_LIMIT = 25


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

@cli.command("find-block", epilog="""\b
Examples:
  logseq-cli --token TOKEN find-block --content "tag support" --page "Project Alpha" --first
  logseq-cli --token TOKEN find-block --content "^### " --page "X" --regex
  logseq-cli --token TOKEN find-block --content "14:57" --page "2026-07-22, tuesday" --with-children
Note:
  Output gives uuid + page + content preview. Use --first to disambiguate; pipe to
  insert-block --child-of, update-block, remove-block downstream.
  --uuid-only prints bare uuids, one per line, and fails when nothing matches.
  For a block to write to, add --exactly-one: it fails unless exactly one block
  matches, where --first would pick one of several without a word on stdout.
    U=$(logseq-cli find-block --content "..." --page "..." --exactly-one --uuid-only)
  --with-children prints each match with its sub-blocks indented, instead of
  guessing a line count with `get-page | grep -A<n>`.
  A common word matches thousands of blocks: --limit N caps the output, and
  whatever is withheld is reported on stderr. --first is --limit 1 with the
  same notice.
""")
@click.option("--content", required=True, help="Content text (substring match or regex with --regex)")
@click.option("--page", "--name", default=None, help="Restrict search to this page name")
@click.option("--regex", "use_regex", is_flag=True, help="Interpret --content as regex pattern")
@click.option("--first", "first_only", is_flag=True, help="Output only the first match")
@click.option("--exactly-one", "exactly_one", is_flag=True, help="Fail unless exactly one block matches, listing the matches otherwise")
@click.option("--limit", "limit", type=int, default=None, help="Print at most N matches (1 or greater); the number withheld is reported on stderr")
@click.option("--with-children", "with_children", is_flag=True, help="Print each match with its sub-blocks (one extra API read per match)")
@click.option("--uuid-only", "uuid_only", is_flag=True, help="Print only the uuids, one per line; exit 1 when nothing matches")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
@handle_connection_error
def find_block(ctx, content, page, use_regex, first_only, exactly_one, limit, with_children, uuid_only, as_json):
    """Find blocks by content substring or regex."""
    api = ctx.obj["api"]

    # Before the query, not after it: the whole result set arrives either way
    # (measured: 71ms, 473KB for 1382 matches), and a value that will be
    # refused must not cost that read first.
    if first_only and limit is not None:
        fail("Specify either --first or --limit, not both.", as_json)
    if limit is not None and limit < 1:
        fail("--limit must be 1 or greater.", as_json)
    if exactly_one and (first_only or limit is not None):
        fail("--exactly-one refuses to pick among matches; drop --first and --limit.", as_json)
    if uuid_only and (as_json or with_children):
        fail("--uuid-only is an output form of its own; drop --json and "
             "--with-children.", as_json)

    if page:
        ref = follow_page(api, page, as_json)
        page = ref.page
    matches = find_blocks_by_content(api, content, page=page, use_regex=use_regex)

    # For a write target, picking one of several matches is a guess, and the
    # caller cannot tell afterwards; resolve_single_block refuses it for the
    # same reason. The matches are listed so the search can be narrowed.
    if exactly_one and len(matches) != 1:
        where = f" on page '{page}'" if page else ""
        if not matches:
            fail(f"No block matches {content!r}{where}.", as_json)
        listing = "\n".join(
            f"  {m.get('uuid')}  {(m.get('content') or '')[:70]}" for m in matches[:10])
        more = f"\n  ... and {len(matches) - 10} more" if len(matches) > 10 else ""
        fail(f"{len(matches)} blocks match {content!r}{where}; --exactly-one refuses "
             f"to pick one. Narrow --content or --page:\n{listing}{more}",
             as_json, matches=[m.get("uuid") for m in matches])

    # A common word matches thousands of blocks, and printing all of them is
    # the unbounded-output failure the journal paths fixed in 0.6.0: the caller
    # hits its response cap and reasons on a fragment without being told. The
    # cut cannot move into the query - DataScript ignores a :limit clause, and
    # the whole result set arrives either way (measured: 71ms, 473KB for 1382
    # matches) - so it happens here, and what was withheld is always named.
    withheld = 0
    if first_only:
        withheld = max(len(matches) - 1, 0)
        matches = matches[:1]
    elif limit is not None and len(matches) > limit:
        withheld = len(matches) - limit
        matches = matches[:limit]

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

    # stdout stays pure payload in both forms, so the notice goes to stderr
    # whether or not --json is set; a caller parsing stdout must still learn
    # that it is holding part of an answer.
    if withheld:
        shown = len(matches)
        click.echo(
            f"showing {shown} of {shown + withheld} match(es) ... {withheld} omitted "
            "(raise --limit, or narrow --content/--page)", err=True)

    if uuid_only:
        # Bare values for $(...). No match is a failure here, unlike the plain
        # form: an empty $U would otherwise flow into the next write unnoticed.
        if not matches:
            fail("No blocks found.")
        for block in matches:
            if block.get("uuid"):
                click.echo(block["uuid"])
        return

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
