import re
import sys

import click

from logseq_cli.config import load_config, resolve_heading
from logseq_cli.group import cli
from logseq_cli.helpers import (
    BlockIdError,
    MultilineContentError,
    append_in_page,
    apply_block_properties,
    check_block_ids,
    check_move,
    check_property_pairs,
    contains_hierarchical_content,
    count_blocks,
    find_heading,
    find_or_create_heading,
    format_journal_date,
    incoming_block_refs,
    insert_block_tree_as_first_children,
    insert_block_tree_as_siblings,
    insert_block_tree_at_page_top,
    insert_block_tree_with_uuids,
    insert_block_at,
    insert_tree_at_page_end,
    move_block_verified,
    outline_text,
    parse_date_keyword,
    parse_hierarchical_content,
    parse_tree_input,
    property_line_mask,
    read_content_file,
    refs_refusal,
    reject_unsupported_multiline,
    require_content,
    require_insert,
    resolve_single_block,
    stored_properties,
    subtree_uuids,
    tree_without_block_ids,
    uuid_fields,
)
from logseq_cli.output import fail, handle_connection_error, output


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
    # honest. They go back as their original text under the keys the database
    # stores (see stored_properties): the block's own map from getBlock has
    # camel-cased keys, and writing those back turned due-date:: into
    # duedate:: and 01234 into 1234. ``id::`` is among them and is written
    # back unchanged, so block references survive.
    values, kept_texts = stored_properties(api, block.get("uuid") or clean_id)
    # Only what has a text goes back. A markdown heading ("## Title") shows up
    # as heading 2 among the values but comes from the "##", not from a line;
    # reporting it as kept would claim a line the file will not have.
    kept_values = {k: v for k, v in values.items() if k in kept_texts}

    if dry_run:
        if as_json:
            output({"id": clean_id, "old_content": old_content,
                    "new_content": content, "properties": kept_values,
                    "dry_run": True}, True)
        else:
            click.echo(f"[DRY RUN] Would overwrite block {clean_id}")
            if old_content:
                preview = old_content[:60] + ("..." if len(old_content) > 60 else "")
                click.echo(f"  was: {preview}")
            preview = content[:60] + ("..." if len(content) > 60 else "")
            click.echo(f"  now: {preview}")
            if kept_texts:
                click.echo(f"  keeps: {', '.join(f'{k}::' for k in kept_texts)}")
        return

    api.update_block(clean_id, content, properties=kept_texts or None)

    if as_json:
        output({"id": clean_id, "old_content": old_content, "new_content": content,
                "properties": kept_values}, True)
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
  Refuses while ((block-refs)) from elsewhere point into the block or its
  children, and lists them; --ignore-refs removes anyway.
""")
@click.option("--id", "block_id", required=True, help="UUID of the block to remove")
@click.option("--ignore-refs", is_flag=True, help="Remove even though ((block-refs)) from elsewhere point into it")
@click.option("--dry-run", is_flag=True, help="Show the block and its descendant count, without deleting")
@click.option("--json", "as_json", is_flag=True, help="JSON output")
@click.pass_context
@handle_connection_error
def remove_block_cmd(ctx, block_id, ignore_refs, dry_run, as_json):
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

    refs = incoming_block_refs(api, uuids=subtree_uuids(block))
    if refs and not ignore_refs:
        fail(refs_refusal(refs, "this block and its children from elsewhere"),
             as_json=as_json, id=clean_id, refs=refs)

    if dry_run:
        if as_json:
            output({"id": clean_id, "content": content,
                    "descendants": descendants, "blocks_removed": descendants + 1,
                    "refs_broken": len(refs), "dry_run": True}, True)
        else:
            click.echo(f"[DRY RUN] Would remove block {clean_id}")
            preview = content[:80] + ("..." if len(content) > 80 else "")
            if preview:
                click.echo(f"  content: {preview}")
            click.echo(f"  descendants that would be removed too: {descendants}")
            click.echo(f"  total blocks affected: {descendants + 1}")
            if refs:
                click.echo(f"  incoming block refs that would dangle: {len(refs)}")
        return

    api.remove_block(clean_id)

    if as_json:
        output({"id": clean_id, "removed": True, "content": content,
                "descendants": descendants, "blocks_removed": descendants + 1,
                "refs_broken": len(refs)}, True)
    else:
        preview = content[:80] + ("..." if len(content) > 80 else "")
        click.echo(f"Removed block {clean_id} ({descendants + 1} block(s) total)")
        if preview:
            click.echo(f"  was: {preview}")
        if refs:
            click.echo(f"  {len(refs)} incoming block ref(s) now point at nothing")

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
            lines = content.split("\n")
            new_lines = [
                ln if is_property else pattern.sub(replace_text, ln)
                for ln, is_property in zip(lines, property_line_mask(lines))
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
  id:: lines (in --tree or --content) are dropped unless --keep-ids is given,
  and the command says so. Use --keep-ids when moving or restoring an outline.
  It restores an id that survives only as a ((ref)) target, and refuses,
  before writing anything, an id a block or page still has (the copy case:
  drop --keep-ids) and a second id:: line in one block.
  An id:: line inside a code block (``` or ~~~) is code and is written as is
  where the fence stays in one block: flat --content, a JSON --tree node.
  Outline text is one block per line, so a fence there is split and its id::
  line counts.
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
@click.option("--property", "properties", multiple=True, help="Set KEY=VALUE property on the created (root) block; repeatable. KEY follows set-property's rule: lower-cased, '_' read as '-', refused if Logseq would drop it")
@click.option("--keep-ids", "keep_ids", is_flag=True, help="Keep the id:: values in the content instead of letting Logseq mint new ones, for moving or restoring an outline; an id only a ((ref)) still holds is restored, so the ref resolves again. Refused before any write: an id a block or page still has, a repeated or malformed one, two in one block")
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
        check_property_pairs(properties)
    except ValueError as e:
        fail(str(e), as_json=as_json)

    if tree_input is not None:
        if content is not None:
            click.echo("Specify either --content or --tree, not both.", err=True)
            sys.exit(1)
        tree = parse_tree_input(tree_input)
        if not tree:
            click.echo("Tree input is empty.", err=True)
            sys.exit(1)

        # An id:: in the tree names a UUID the block is meant to keep; the
        # contract (announce, or check and keep) lives in check_block_ids.
        try:
            note = check_block_ids(api, tree, keep_ids)
        except BlockIdError as e:
            fail(str(e), as_json=as_json, **{e.field: e.ids})
        if note:
            click.echo(note, err=True)
            tree = tree_without_block_ids(tree)

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
    # The outline that is written, checked and cleaned as it is: flat content
    # is one block, whatever its lines look like.
    hierarchical = contains_hierarchical_content(content)
    tree = (parse_hierarchical_content(content) if hierarchical
            else [{"content": content, "children": []}])
    try:
        note = check_block_ids(api, tree, keep_ids)
    except BlockIdError as e:
        fail(str(e), as_json=as_json, **{e.field: e.ids})
    if note:
        click.echo(note, err=True)
        tree = tree_without_block_ids(tree)
        # What the flat writes below send, and what the preview shows.
        content = outline_text(tree) if hierarchical else tree[0]["content"]

    if dry_run:
        planned = count_blocks(tree)
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
            uuids = insert_tree_at_page_end(api, page, tree, keep_ids=keep_ids)
            new_uuid = uuids[0] if uuids else None
            result = {"blocks_added": len(uuids), "uuids": uuids}
            position = f"end of '{page}' ({len(uuids)} block(s))"
        else:
            result = append_in_page(api, page, content, keep_ids)
            new_uuid = require_insert(result, f"a block in '{page}'")
            position = f"end of '{page}'"
    elif after:
        clean_id = after.strip().replace("((", "").replace("))", "")
        if hierarchical:
            uuids = insert_block_tree_as_siblings(api, tree, clean_id, before=False, keep_ids=keep_ids)
            new_uuid = uuids[0] if uuids else None
            result = {"blocks_added": len(uuids), "uuids": uuids}
            position = f"after {clean_id[:8]}... ({len(uuids)} block(s))"
        else:
            result = insert_block_at(api, clean_id, content, sibling=True, before=False, keep_ids=keep_ids)
            new_uuid = require_insert(result, f"a block after {clean_id[:8]}...")
            position = f"after {clean_id[:8]}..."
    elif before:
        clean_id = before.strip().replace("((", "").replace("))", "")
        if hierarchical:
            uuids = insert_block_tree_as_siblings(api, tree, clean_id, before=True, keep_ids=keep_ids)
            new_uuid = uuids[0] if uuids else None
            result = {"blocks_added": len(uuids), "uuids": uuids}
            position = f"before {clean_id[:8]}... ({len(uuids)} block(s))"
        else:
            result = insert_block_at(api, clean_id, content, sibling=True, before=True, keep_ids=keep_ids)
            new_uuid = require_insert(result, f"a block before {clean_id[:8]}...")
            position = f"before {clean_id[:8]}..."
    elif child_of:
        clean_id = child_of.strip().replace("((", "").replace("))", "")
        where = "first child" if as_first else "child"
        if hierarchical:
            if as_first:
                uuids = insert_block_tree_as_first_children(api, tree, clean_id, keep_ids=keep_ids)
            else:
                uuids = insert_block_tree_with_uuids(api, tree, clean_id, strict=True, keep_ids=keep_ids)
            new_uuid = uuids[0] if uuids else None
            result = {"blocks_added": len(uuids), "uuids": uuids}
            position = f"{where} of {clean_id[:8]}... ({len(uuids)} block(s))"
        else:
            result = insert_block_at(api, clean_id, content, sibling=False, before=as_first,
                                     keep_ids=keep_ids)
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
        # Before the preview: the content may itself contain "uuid: ...".
        if new_uuid:
            click.echo(f"  uuid: {new_uuid}")
        preview = content[:80] + ("..." if len(content) > 80 else "")
        click.echo(f"  {preview}")
        for key, value in applied.items():
            click.echo(f"  {key}:: {value}")

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

@cli.command("copy-block", epilog="""\b
Examples:
  logseq-cli --token TOKEN copy-block --id UUID --to-page "Target Page"
  logseq-cli --token TOKEN copy-block --id UUID --to-page "Target Page" --remove
Note:
  Copies block + all children. With --remove: original is deleted (move).
  The copy gets new UUIDs, so --remove refuses while ((block-refs)) point
  into the original, even from within it; move-block keeps the UUIDs.
""")
@click.option("--id", "block_id", required=True, help="Source block UUID")
@click.option("--to-page", required=True, help="Target page name")
@click.option("--remove", is_flag=True, help="Remove source block after copying (move)")
@click.option("--ignore-refs", is_flag=True, help="With --remove: remove even though ((block-refs)) point into the source")
@click.option("--dry-run", is_flag=True, help="Show what would be copied/moved, without writing")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
@click.pass_context
@handle_connection_error
def copy_block(ctx, block_id, to_page, remove, ignore_refs, dry_run, as_json):
    """Copy a block (with children) to another page."""
    api = ctx.obj["api"]
    block_id = block_id.strip("()")
    source = api.get_block(block_id, include_children=True)
    if not source:
        fail("Block not found.", as_json=as_json, id=block_id)

    # Checked before the copy is written: refusing afterwards would leave a
    # copy behind that nobody asked to keep.
    refs = (incoming_block_refs(api, uuids=subtree_uuids(source), count_inside=True)
            if remove else [])
    if refs and not ignore_refs:
        fail(refs_refusal(refs, "the source, from elsewhere or from within it",
                          "The copy gets new UUIDs; move-block keeps them. "),
             as_json=as_json, id=block_id, refs=refs)

    if dry_run:
        planned = count_blocks([source])
        action = "move" if remove else "copy"
        content = source.get("content", "") if isinstance(source, dict) else ""
        if as_json:
            output({"action": action, "blocks": planned, "to_page": to_page,
                    "source_id": block_id, "removes_source": bool(remove),
                    "refs_broken": len(refs), "dry_run": True}, True)
        else:
            click.echo(f"[DRY RUN] Would {action} {planned} block(s) to '{to_page}'")
            preview = content[:80] + ("..." if len(content) > 80 else "")
            if preview:
                click.echo(f"  root: {preview}")
            if remove:
                click.echo(f"  source block {block_id} WOULD BE REMOVED after copying")
            if refs:
                click.echo(f"  block refs into the source that would dangle: {len(refs)}")
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
    if remove:
        result_data["refs_broken"] = len(refs)

    if as_json:
        output(result_data, True)
    else:
        click.echo(f"{action} {count} block(s) to '{to_page}'.")
        if refs:
            click.echo(f"  {len(refs)} block ref(s) into the source now point at nothing")

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
  A target inside the block's own subtree is refused before anything moves,
  and --dry-run refuses it (and a missing target) the same way.
  Logseq answers every move with null, so the move is verified by re-reading
  and reported as an error if it did not take.
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
        check_move(api, block_id, target)
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


cli.add_command(remove_block_cmd, "delete-block")
