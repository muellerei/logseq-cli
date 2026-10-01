import re
import sys

import click

from logseq_cli.blockprops import apply_block_properties, check_property_pairs, kept_properties
from logseq_cli.blocktext import (
    property_line_mask,
    refuse_id_lines,
    refuse_split_block,
    refuse_split_heading,
    refuse_split_tree,
    unwrap_block_id,
    without_block_ids,
)
from logseq_cli.cliinput import (
    content_or_file,
    parse_tree_input,
    read_content_file,
    require_content,
)
from logseq_cli.config import keep_empty_blocks_last, load_config, resolve_heading
from logseq_cli.dates import parse_date_keyword
from logseq_cli.group import cli
from logseq_cli.headings import first_empty_under, find_heading, find_or_create_heading
from logseq_cli.ids import (
    BlockIdError,
    check_block_ids,
    require_text_besides_ids,
    tree_without_block_ids,
    without_foreign_block_ids,
)
from logseq_cli.lookup import incoming_block_refs, refs_refusal, resolve_single_block
from logseq_cli.notes import print_note
from logseq_cli.outlinetext import (
    contains_hierarchical_content,
    count_blocks,
    note_quote_breaks,
    outline_text,
    parse_hierarchical_content,
    subtree_uuids,
)
from logseq_cli.output import (
    fail,
    before_empty_fields,
    follow_page,
    follow_page_to_write,
    handle_connection_error,
    output,
    uuid_fields,
    would_go_before_fields,
)
from logseq_cli.safety import WriteCommand
from logseq_cli.pagenames import PageToWrite, journal_page_name
from logseq_cli.strictinsert import (
    BEFORE_EMPTY_SUFFIX,
    append_in_page,
    check_move,
    create_missing_page,
    before_first_empty,
    before_empty_or_append,
    first_empty_at_end,
    insert_block_at,
    insert_block_tree_as_first_children,
    insert_block_tree_as_siblings,
    insert_block_tree_at_page_top,
    insert_block_tree_with_uuids,
    insert_tree_at_page_end,
    move_block_verified,
)
from logseq_cli.writerefused import WriteRefused, partial_state


@cli.command("update-block", cls=WriteCommand, epilog="""\b
Example:
  logseq-cli --token TOKEN update-block --id 12345678-... --content "New text"
  logseq-cli --token TOKEN update-block --where-content "**14:22**" --page "2026-08-21, friday" --content "New text"
Note:
  Use set-property/remove-property for properties, never edit them via update-block.
  Existing block properties survive the update: they are read first and written
  back, so changing the text no longer drops them. A property line in
  --content is the new value of its key.
  Use set-todo-status to change TODO/DOING/DONE markers.
  --content is ONE block, so a line Logseq would read as a block of its own is
  refused: a "- " or "# " line after the first (indented too), or a code fence
  nothing closes. In a closed code block such lines are fine. Children go in
  via insert-block --child-of UUID.
  --where-content selects the block by text instead of UUID; it aborts unless
  exactly one block matches, since overwriting the wrong block loses its text.
  Scope it with --page and check with --dry-run.
  --content-file FILE is --content read from a file ('-' reads stdin), with
  the same rules; no shell quoting stands between the text and the command.
  A quote ends at a blank line, and the paragraph after it shows as plain
  text: written anyway, with a Note on stderr. Start the blank line with ">"
  to keep the paragraph in the quote.
  The block's own id:: line (as get-block shows it) may stay in --content;
  one naming another uuid is dropped with a Note, since Logseq would make
  it this block's uuid and every ((ref)) to the block would dangle.
""")
@click.option("--id", "block_id", default=None, help="UUID of the block to update")
@click.option("--where-content", "where_content", default=None, help="Select the block by content instead of --id; must match exactly one")
@click.option("--page", "--name", "page", default=None, help="With --where-content: restrict the search to this page")
@click.option("--regex", "use_regex", is_flag=True, help="With --where-content: interpret it as a regex")
@click.option("--content", default=None, help="New content for the block; this or --content-file is required")
@click.option("--content-file", "content_file", default=None, help="Read --content from this file instead ('-' reads stdin), so apostrophes, quotes and umlauts need no shell quoting. Mutually exclusive with --content")
@click.option("--dry-run", is_flag=True, help="Show the block that would be overwritten, without writing")
@click.option("--json", "as_json", is_flag=True, help="JSON output")
@click.pass_context
@handle_connection_error
def update_block(ctx, block_id, where_content, page, use_regex, content, content_file, dry_run, as_json):
    """Update the content of an existing block."""
    content = content_or_file(content, content_file)
    # This command has no tree path: it replaces ONE block's content, so a
    # line that Logseq reads as a block of its own is refused (#47). Unlike
    # set-todo-status and replace-text, which change part of a block, it gets
    # no pass for such a line the block already had: the whole text is the
    # caller's, and a line in it is written because the caller sent it.
    refuse_split_block(content, command="update-block")

    api = ctx.obj["api"]
    require_content(content)
    if bool(block_id) == bool(where_content):
        fail("Specify exactly one of: --id, --where-content.", as_json=as_json)
    if where_content:
        if page:
            ref = follow_page(api, page, as_json)
            page = ref.page
        clean_id = resolve_single_block(api, where_content, page=page, use_regex=use_regex)
    else:
        clean_id = unwrap_block_id(block_id)

    # Verify block exists
    block = api.get_block(clean_id, include_children=False)
    if not block:
        fail(f"Block not found: {clean_id}", as_json=as_json, id=clean_id)

    old_content = block.get("content", "") if isinstance(block, dict) else ""
    # The block's own id:: line, as getBlock hands it out, goes back as it
    # came; one naming another uuid is dropped, as every writer drops it (#56).
    content, id_note = without_foreign_block_ids(content, block.get("uuid") or clean_id)
    if id_note:
        require_text_besides_ids(content)
    # Properties are stored inside the block content, so replacing the text
    # would drop them. This command changes text; properties belong to
    # set-block-property / remove-property, and losing them here was a silent
    # side effect nobody asked for. Carrying them through keeps that split
    # honest; kept_properties says which go back, and as what (#30, #66).
    kept_values, kept_texts = kept_properties(api, block.get("uuid") or clean_id, content)

    # After every check that can refuse: a note ahead of an error would speak
    # of text that is never written.
    if id_note:
        print_note(id_note)
    note_quote_breaks([{"content": content}])
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

@cli.command("remove-block", cls=WriteCommand, epilog="""\b
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
    clean_id = unwrap_block_id(block_id)

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

@cli.command("replace-text", cls=WriteCommand, epilog="""\b
Example:
  logseq-cli --token TOKEN replace-text --page "X" --find "old" --replace "new" --dry-run
  logseq-cli --token TOKEN replace-text --page "X" --find "old" --replace "new"
Note:
  ALWAYS run with --dry-run first to preview matches. Prefer set-todo-status
  for TODO->DONE transitions and update-block for block content edits.
  A replacement that gives a block a line Logseq would read as a block of its
  own ("- " or "# " at a line start, an unclosed code fence) is refused before
  any block is written; it may change such lines the block had, not add one.
  So is one that turns a line into an id:: line, which Logseq would make the
  block's uuid.
  --replace is inserted as written. With --regex it is a template: \\1 or
  \\g<name> for a group, a backslash doubled to write one.
""")
@click.option("--page", "--name", required=True, help="Page name to search in")
@click.option("--find", "find_text", required=True, help="Text to find")
@click.option("--replace", "replace_text", required=True, help="Replacement text, literal unless --regex")
@click.option("--regex", "use_regex", is_flag=True, help="Treat --find as regex pattern")
@click.option("--dry-run", is_flag=True, help="Show matches without replacing")
@click.option("--json", "as_json", is_flag=True, help="JSON output")
@click.pass_context
@handle_connection_error
def replace_text(ctx, page, find_text, replace_text, use_regex, dry_run, as_json):
    """Find and replace text in all blocks of a page."""
    api = ctx.obj["api"]

    ref = follow_page(api, page, as_json)
    page = ref.page
    blocks = api.get_page_blocks_tree(page)
    if not blocks:
        fail(f"Page '{page}' not found or empty.", as_json=as_json, page=page)

    if use_regex:
        try:
            pattern = re.compile(find_text)
        # A repeat count past the C limit or deep nesting fails outside re.error.
        except (re.error, OverflowError, RecursionError) as e:
            fail(f"--find is not a valid regex: {e}", as_json=as_json)
        # re.sub parses the template before it searches, so an empty string
        # checks it whether or not any block matches. A name no group has is
        # an IndexError, not a re.error.
        try:
            pattern.sub(replace_text, "")
        except (re.error, IndexError) as e:
            fail(f"--replace is not a valid template for --find: {e}", as_json=as_json)
        replacement = replace_text
    else:
        pattern = re.compile(re.escape(find_text))
        # A string replacement is always a template to re.sub, so "C:\new"
        # would write a line break (#60). A function's result is taken as is.
        def replacement(_match):
            return replace_text

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
                ln if is_property else pattern.sub(replacement, ln)
                for ln, is_property in zip(lines, property_line_mask(lines))
            ]
            new_content = "\n".join(new_lines)
            if new_content != content:
                replacements.append({
                    "id": uuid,
                    "old": content,
                    "new": new_content,
                })
            children = block.get("children", [])
            if children:
                scan_blocks(children)

    scan_blocks(blocks)

    # Every replacement is checked before the first is written, so a refusal
    # leaves no block half done (#47). Lines a block already had may stay.
    for r in replacements:
        where = f"The block {r['id'][:8]}.. after the replacement"
        refuse_split_block(r["new"], command="replace-text", replacing=r["old"], where=where)
        # A text line turned into an id:: line would give the block another
        # uuid (#56); the property lines it had are masked above and stay.
        refuse_id_lines(r["new"], own=r["id"], replacing=r["old"], where=where)
    # update_block proves each write itself and raises if it cannot. The one
    # caller that catches: its contract is a report of every block, so a
    # block open in the editor, one Logseq threw on or one it did not write
    # fails alone, and the others are still written.
    reasons = {}
    if not dry_run:
        for r in replacements:
            try:
                api.update_block(r["id"], r["new"], replacing=r["old"])
            except WriteRefused as refused:
                reasons[r["id"]] = refused.reason
    failed = list(reasons)

    if as_json:
        payload = {**ref.fields(), "replacements": len(replacements) - len(failed),
                   "dry_run": dry_run, "matches": replacements}
        if failed:
            payload["failed"] = failed
            payload["failed_reasons"] = reasons
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
                mark = f"  !! not written ({reasons[r['id']]})" if r["id"] in reasons else ""
                click.echo(f"  {r['id'][:8]}..  {old_preview}{mark}")
                click.echo(f"         →  {new_preview}")
    # After the report, in both modes: the payload says which blocks changed,
    # and a caller who stops at the exit status must not read 0 as done.
    if failed:
        # One reason when every block failed for the same one, else the
        # general one; each block's own is in failed_reasons.
        common = set(reasons.values())
        fail(f"{len(failed)} of {len(replacements)} replacement(s) were not "
             f"written or did not show in Logseq. {partial_state(api.writes_landed)}",
             as_json=as_json,
             reason=common.pop() if len(common) == 1 else "write_not_verified",
             failed=failed, failed_reasons=reasons, writes_landed=api.writes_landed)

@cli.command("insert-block", cls=WriteCommand, epilog="""\b
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
  --content (or --content-file), --tree and --tree-file are mutually exclusive.
  --tree-file reads the same tab-indented text (or JSON) from a file, so
  apostrophes/quotes/umlauts need no shell quoting.
  --child-of UUID also accepts hierarchical --content (same tab-indent format).
  --first puts the block at the HEAD of the child list instead of appending it
  last; it only applies together with --child-of.
  id:: lines (in --tree or --content) are dropped unless --keep-ids is given,
  and the command says so; text that is nothing but id:: lines is refused.
  Use --keep-ids when moving or restoring an outline.
  It restores an id that survives only as a ((ref)) target, and refuses,
  before writing anything, an id a block or page still has (the copy case:
  drop --keep-ids) and a second id:: line in one block.
  An id:: line inside a code block (``` or ~~~) is code and is written as is.
  Text written as ONE block (--content without indentation, a --tree node)
  is refused if Logseq would read a line of it as a block of its own: a "- "
  or "# " line after the first, or a code fence nothing closes. In a closed
  code block such lines are fine.
  --content-file FILE is --content read from a file ('-' reads stdin), with
  the same rules; no shell quoting stands between the text and the command.
  A quote ends at a blank line, and the paragraph after it shows as plain
  text: written anyway, with a Note on stderr. Start the blank line with ">"
  to keep the paragraph in the quote.
  [graph] keep_empty_blocks_last (or LOGSEQ_CLI_KEEP_EMPTY_BLOCKS_LAST): a write that ends
  a section goes before the empty blocks that end it, so they stay last, and
  nothing is overwritten.
""")
@click.option("--page", "--name", default=None, help="Page name (append to end of page)")
@click.option("--after", default=None, help="UUID of block to insert after (as sibling)")
@click.option("--before", default=None, help="UUID of block to insert before (as sibling)")
@click.option("--child-of", default=None, help="UUID of parent block (insert as child)")
@click.option("--first", "as_first", is_flag=True, help="With --child-of: insert as FIRST child instead of appending last")
@click.option("--top-level", is_flag=True, help="With --page and --tree: insert at page top-level")
@click.option("--content", default=None, help="Content for the new block; one of --content, --content-file, --tree, --tree-file is required")
@click.option("--content-file", "content_file", default=None, help="Read --content from this file instead ('-' reads stdin), so apostrophes, quotes and umlauts need no shell quoting. Mutually exclusive with --content")
@click.option("--tree", "tree_input", default=None, help="Tab-indented hierarchy or JSON array of {content, children} nodes")
@click.option("--tree-file", "tree_file", default=None, help="Read the tree (tab-indented text or JSON) from a file. Mutually exclusive with --tree and --content.")
@click.option("--property", "properties", multiple=True, help="Set KEY=VALUE property on the created (root) block; repeatable. KEY follows set-property's rule: lower-cased, '_' read as '-', refused if Logseq would drop it")
@click.option("--keep-ids", "keep_ids", is_flag=True, help="Keep the id:: values in the content instead of letting Logseq mint new ones, for moving or restoring an outline; an id only a ((ref)) still holds is restored, so the ref resolves again. Refused before any write: an id a block or page still has, a repeated or malformed one, two in one block")
@click.option("--dry-run", is_flag=True, help="Show what would be inserted (block count + position) without writing")
@click.option("--quiet", is_flag=True, help="With --tree: print only the confirmation line, not one uuid line per block")
@click.option("--json", "as_json", is_flag=True, help="JSON output")
@click.pass_context
@handle_connection_error
def insert_block_cmd(ctx, page, after, before, child_of, as_first, top_level, content, content_file, tree_input, tree_file, properties, keep_ids, dry_run, quiet, as_json):
    """Insert a block (or tree of blocks) at a specific position."""
    api = ctx.obj["api"]
    content = content_or_file(content, content_file, required=False)
    # --page is the target only without an anchor: with --after, --before or
    # --child-of it is refused or, in tree mode, not used, and must not be
    # resolved into an alias_of for a page nothing is written to.
    alias = {}
    target = None
    if page and not (after or before or child_of):
        # A missing page is written under the name Logseq creates it with: a
        # journal title in another format is the journal (measured).
        ref, target = follow_page_to_write(api, page, as_json)
        alias = {"alias_of": ref.page} if ref.redirected else {}
        page = target.name

    # --tree-file is --tree from a file; resolve it before any other validation
    # so the rest of the command sees a single tree_input.
    if tree_file is not None:
        if tree_input is not None:
            click.echo("Specify either --tree or --tree-file, not both.", err=True)
            sys.exit(1)
        tree_input = read_content_file(tree_file, option="--tree-file")

    # Validate property pairs up-front so a bad pair fails before any write.
    try:
        check_property_pairs(properties)
    except ValueError as e:
        fail(str(e), as_json=as_json)

    if tree_input is not None:
        if content is not None:
            tree_option = "--tree-file" if tree_file is not None else "--tree"
            click.echo(f"Specify either --content (or --content-file) or {tree_option}, not both.", err=True)
            sys.exit(1)
        tree = parse_tree_input(tree_input)
        if not tree:
            click.echo("Tree input is empty.", err=True)
            sys.exit(1)
        refuse_split_tree(tree, command="insert-block --tree", label="The --tree node",
                          single_label="The --tree node")

        # An id:: in the tree names a UUID the block is meant to keep; the
        # contract (announce, or check and keep) lives in check_block_ids.
        try:
            note = check_block_ids(api, tree, keep_ids)
        except BlockIdError as e:
            fail(str(e), as_json=as_json, **{e.field: e.ids})
        if note:
            print_note(note)
            tree = tree_without_block_ids(tree)

        # What [graph] keep_empty_blocks_last acts on: the writes that end a section.
        keep_on = keep_empty_blocks_last(load_config())
        # The empty block a write went before, for what the command reports.
        ahead_of = {}
        end_target = {}

        # Resolve target + position first (no writes), so --dry-run can report
        # the plan and bail before touching the graph.
        if child_of:
            clean_id = unwrap_block_id(child_of)
            position = f"{'first child' if as_first else 'child'} of {clean_id[:8]}..."
            if as_first:
                def do_insert():
                    return insert_block_tree_as_first_children(api, tree, clean_id, keep_ids=keep_ids)
            else:
                end_target = {"parent_uuid": clean_id}

                def do_insert():
                    uuids, ahead_of["uuid"] = before_empty_or_append(
                        api, tree, lambda: insert_block_tree_with_uuids(
                            api, tree, clean_id, keep_ids=keep_ids),
                        keep_last=keep_on, keep_ids=keep_ids, **end_target)
                    return uuids
        elif after:
            clean_id = unwrap_block_id(after)
            position = f"after {clean_id[:8]}..."
            def do_insert():
                return insert_block_tree_as_siblings(api, tree, clean_id, before=False, keep_ids=keep_ids)
        elif before:
            clean_id = unwrap_block_id(before)
            position = f"before {clean_id[:8]}..."
            def do_insert():
                return insert_block_tree_as_siblings(api, tree, clean_id, before=True, keep_ids=keep_ids)
        elif page and top_level:
            position = f"top-level of '{page}'"
            end_target = {"page_name": page}

            def do_insert():
                uuids, ahead_of["uuid"] = before_empty_or_append(
                    api, tree, lambda: insert_block_tree_at_page_top(
                        api, tree, page, keep_ids=keep_ids),
                    keep_last=keep_on, keep_ids=keep_ids, **end_target)
                return uuids
        else:
            click.echo(
                "Tree insert requires --child-of, --after, --before, or --page NAME --top-level",
                err=True,
            )
            sys.exit(1)

        note_quote_breaks(tree)
        if dry_run:
            if child_of or after or before:
                refuse_missing_anchor(api, clean_id, as_json)
            planned = count_blocks(tree)
            would_anchor = (first_empty_at_end(api, tree, **end_target)
                          if keep_on and end_target else None)
            if as_json:
                output({"position": position, "blocks": planned, "dry_run": True,
                        **would_go_before_fields(would_anchor),
                        **alias}, True)
            else:
                click.echo(f"[DRY RUN] Would insert {planned} block(s) {position}")
                if would_anchor:
                    click.echo(f"  would go before the empty block {would_anchor[:8]}...")
            return

        if page and top_level:
            create_missing_page(api, target)
        # The tree is not empty (checked above), and every insert is proven,
        # so there is a first block.
        uuids = do_insert()
        applied = apply_block_properties(api, uuids[0], properties) if properties else {}

        if as_json:
            output({
                "position": position,
                **uuid_fields(uuids),
                "blocks_added": len(uuids),
                "properties": applied,
                **before_empty_fields(ahead_of.get("uuid")),
                **alias,
            }, True)
        else:
            click.echo(f"Inserted {len(uuids)} block(s) {position}"
                       f"{BEFORE_EMPTY_SUFFIX if ahead_of.get('uuid') else ''}")
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
        click.echo("Specify --content, --content-file, --tree or --tree-file.", err=True)
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
    refuse_split_tree(tree, command="insert-block")
    try:
        note = check_block_ids(api, tree, keep_ids)
    except BlockIdError as e:
        fail(str(e), as_json=as_json, **{e.field: e.ids})
    if note:
        print_note(note)
        tree = tree_without_block_ids(tree)
        # What the flat writes below send, and what the preview shows.
        content = outline_text(tree) if hierarchical else tree[0]["content"]

    note_quote_breaks(tree)
    # [graph] keep_empty_blocks_last acts on the writes that end a section: the end
    # of a page and a last child.
    keep_on = keep_empty_blocks_last(load_config())
    end_target = ({"page_name": page} if page else
                  {"parent_uuid": unwrap_block_id(child_of)} if child_of and not as_first
                  else {})
    ahead_of = {}
    if dry_run:
        if not page:
            refuse_missing_anchor(api, after or before or child_of, as_json)
        planned = count_blocks(tree)
        target_desc = (f"end of '{page}'" if page else
                       f"after {after[:8]}..." if after else
                       f"before {before[:8]}..." if before else
                       f"{'first child' if as_first else 'child'} of {child_of[:8]}...")
        would_anchor = (first_empty_at_end(api, tree, **end_target)
                      if keep_on and end_target else None)
        if as_json:
            output({"position": target_desc, "blocks": planned, "dry_run": True,
                    **would_go_before_fields(would_anchor),
                    **alias}, True)
        else:
            click.echo(f"[DRY RUN] Would insert {planned} block(s) {target_desc}")
            if would_anchor:
                click.echo(f"  would go before the empty block {would_anchor[:8]}...")
        return

    written = None
    if page:
        create_missing_page(api, target)
    if keep_on and end_target:
        written, ahead_of["uuid"] = before_first_empty(api, tree, keep_ids=keep_ids, **end_target)

    if page:
        if hierarchical:
            uuids = written if written is not None else insert_tree_at_page_end(
                api, page, tree, keep_ids=keep_ids)
            new_uuid = uuids[0]
            result = {"blocks_added": len(uuids), "uuids": uuids}
            position = f"end of '{page}' ({len(uuids)} block(s))"
        else:
            result = (api.get_block(written[0], include_children=False) or {"uuid": written[0]}
                      if written is not None
                      else append_in_page(api, page, content, keep_ids))
            new_uuid = result["uuid"]
            position = f"end of '{page}'"
    elif after:
        clean_id = unwrap_block_id(after)
        if hierarchical:
            uuids = insert_block_tree_as_siblings(api, tree, clean_id, before=False, keep_ids=keep_ids)
            new_uuid = uuids[0]
            result = {"blocks_added": len(uuids), "uuids": uuids}
            position = f"after {clean_id[:8]}... ({len(uuids)} block(s))"
        else:
            result = insert_block_at(api, clean_id, content, sibling=True, before=False, keep_ids=keep_ids)
            new_uuid = result["uuid"]
            position = f"after {clean_id[:8]}..."
    elif before:
        clean_id = unwrap_block_id(before)
        if hierarchical:
            uuids = insert_block_tree_as_siblings(api, tree, clean_id, before=True, keep_ids=keep_ids)
            new_uuid = uuids[0]
            result = {"blocks_added": len(uuids), "uuids": uuids}
            position = f"before {clean_id[:8]}... ({len(uuids)} block(s))"
        else:
            result = insert_block_at(api, clean_id, content, sibling=True, before=True, keep_ids=keep_ids)
            new_uuid = result["uuid"]
            position = f"before {clean_id[:8]}..."
    elif child_of:
        clean_id = unwrap_block_id(child_of)
        where = "first child" if as_first else "child"
        if hierarchical:
            if as_first:
                uuids = insert_block_tree_as_first_children(api, tree, clean_id, keep_ids=keep_ids)
            elif written is not None:
                uuids = written
            else:
                uuids = insert_block_tree_with_uuids(api, tree, clean_id, keep_ids=keep_ids)
            new_uuid = uuids[0]
            result = {"blocks_added": len(uuids), "uuids": uuids}
            position = f"{where} of {clean_id[:8]}... ({len(uuids)} block(s))"
        else:
            result = (api.get_block(written[0], include_children=False) or {"uuid": written[0]}
                      if written is not None
                      else insert_block_at(api, clean_id, content, sibling=False,
                                           before=as_first, keep_ids=keep_ids))
            new_uuid = result["uuid"]
            position = f"{where} of {clean_id[:8]}..."

    # Every branch above set new_uuid from a proven insert.
    applied = apply_block_properties(api, new_uuid, properties) if properties else {}

    if as_json:
        output({"position": position, "content": content, "result": result, "properties": applied, **uuid_fields([new_uuid]),
                **before_empty_fields(ahead_of.get("uuid")), **alias}, True)
    else:
        click.echo(f"Inserted block {position}{BEFORE_EMPTY_SUFFIX if ahead_of.get('uuid') else ''}")
        # Before the preview: the content may itself contain "uuid: ...".
        click.echo(f"  uuid: {new_uuid}")
        preview = content[:80] + ("..." if len(content) > 80 else "")
        click.echo(f"  {preview}")
        for key, value in applied.items():
            click.echo(f"  {key}:: {value}")

def refuse_missing_anchor(api, anchor, as_json):
    """Refuse an insert-block preview whose anchor no block has.

    The run finds out by writing: Logseq answers an insert at an unknown
    uuid with null (write_not_verified), and a tree or a --keep-ids write
    reads the anchor first. The preview writes nothing, so it asks here, and
    does not promise an insert the run refuses (move-block's preview shares
    check_move for the same reason). getBlock finds a uuid in capitals too.
    """
    uuid = unwrap_block_id(anchor).lower()
    if not api.get_block(uuid, include_children=False):
        fail(f"Cannot insert: block {uuid[:8]}... not found (the uuid does not "
             "exist, or its page is not loaded). Nothing was written.",
             as_json=as_json, reason="block_not_found", id=uuid)


@cli.command("add-block-ref", cls=WriteCommand, epilog="""\b
Examples:
  logseq-cli --token TOKEN add-block-ref --source-id UUID --under-heading "## Tasks"
  logseq-cli --token TOKEN add-block-ref --source-id UUID --journal-date 2026-04-23 \\
                                          --under-heading "## Tasks"
  logseq-cli --token TOKEN add-block-ref --source-id UUID --page "Project Alpha" \\
                                          --under-heading "## Open TODOs"
Note:
  Default target: today's journal. Auto-creates the journal page if missing.
  [graph] keep_empty_blocks_last (or LOGSEQ_CLI_KEEP_EMPTY_BLOCKS_LAST): a write that ends
  a section goes before the empty blocks that end it, so they stay last, and
  nothing is overwritten.
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
    config = load_config()
    under_heading = resolve_heading(config, under_heading)
    keep_on = keep_empty_blocks_last(config)
    refuse_split_heading(under_heading, command="add-block-ref")
    source_id = source_id.strip().strip("()").strip()
    # A ref to no block renders as nothing, and the TODO it carries over looks
    # linked and is not (#70). Asked before the page or the heading is written.
    # get_block answers None for the placeholder of a dead ref and for a
    # page's uuid too (api.block_or_none).
    source_block = api.get_block(source_id, include_children=False)
    if not source_block:
        # repr: an invisible character copied along shows, as does a line break.
        fail(f"No block has the uuid {source_id!r}: a ref to it would render as "
             "nothing. Nothing was written.", as_json=as_json, source_id=source_id)
    # The uuid as Logseq holds it: getBlock also finds one in capitals, and
    # whatever else came with the input would make the ref no ref.
    source_id = source_block["uuid"]
    ref_content = f"(({source_id}))"

    if not journal_date and not page:
        # Default: today's journal
        import datetime as _dt
        journal_date = _dt.date.today().strftime("%Y-%m-%d")

    # A page named by the caller means what it means in Logseq (#63); a
    # journal named by its date has no alias to follow.
    names = {}
    if page:
        # A missing page under the name Logseq creates it with, as for
        # insert-block --page.
        ref, target = follow_page_to_write(api, page, as_json)
        names = ref.fields()
    else:
        d = parse_date_keyword(journal_date)
        name = journal_page_name(api, d)
        # Ensure journal page exists
        try:
            existing = api.get_page(name)
        except Exception:
            existing = None
        target = PageToWrite(name, name, existing)
    page = target.name

    # Creating the page is itself a write, so under --dry-run it is only
    # reported, never done.
    would_create_page = not target.page
    if not dry_run:
        create_missing_page(api, target)


    if dry_run:
        source_content = source_block.get("content", "")
        # Look the heading up WITHOUT creating it — find_or_create_heading would
        # append it to the page and make the preview a write.
        heading_exists = (find_heading(api, page, under_heading) is not None
                          if under_heading and not would_create_page else False)
        # The empty block [graph] keep_empty_blocks_last would put the ref before;
        # none when the heading is missing, which would go there itself.
        would_anchor = (first_empty_under(api, [{"content": ref_content, "children": []}],
                                      page, under_heading)
                      if keep_on and not would_create_page else None)
        if under_heading:
            position = f"under '{under_heading}' on '{page}'"
        else:
            position = f"top-level on '{page}'"

        if as_json:
            output({"source_id": source_id, "ref": ref_content, **(names or {"page": page}),
                    "position": position, "under_heading": under_heading,
                    # Kept for callers that read it; a missing source fails above.
                    "source_exists": True,
                    "source_content": source_content,
                    "would_create_page": would_create_page,
                    "would_create_heading": bool(under_heading) and not heading_exists,
                    **would_go_before_fields(would_anchor),
                    "dry_run": True}, True)
        else:
            click.echo(f"[DRY RUN] Would add block-ref {position}")
            click.echo(f"  ref: {ref_content}")
            preview = source_content[:60] + ("..." if len(source_content) > 60 else "")
            click.echo(f"  source: {preview}")
            click.echo(f"  target page: {page}"
                       f"{' (would be created)' if would_create_page else ''}")
            if under_heading:
                click.echo(f"  heading: {under_heading}"
                           f"{'' if heading_exists else ' (would be created)'}")
            if would_anchor:
                click.echo(f"  would go before the empty block {would_anchor[:8]}...")
        return

    # A ref that was never written is worse than a visible error: the TODO looks
    # linked on the project page and silently is not, which is exactly what
    # block-refs are relied on for. The API proves each insert and raises.
    ref_node = [{"content": ref_content, "children": []}]
    if under_heading:
        heading_uuid = find_or_create_heading(api, page, under_heading, keep_last=keep_on)
        uuids, ahead_of = before_empty_or_append(
            api, ref_node, lambda: [api.insert_block(heading_uuid, ref_content,
                                                     {"sibling": False})["uuid"]],
            keep_last=keep_on, parent_uuid=heading_uuid)
        position = f"under '{under_heading}' on '{page}'"
    else:
        uuids, ahead_of = before_empty_or_append(
            api, ref_node, lambda: [api.append_block_in_page(page, ref_content)["uuid"]],
            keep_last=keep_on, page_name=page)
        position = f"top-level on '{page}'"
    new_uuid = uuids[0]

    if as_json:
        output({"source_id": source_id, "ref": ref_content, **(names or {"page": page}), "position": position, "uuid": new_uuid,
                **before_empty_fields(ahead_of)}, True)
    else:
        click.echo(f"Added block-ref {position}{BEFORE_EMPTY_SUFFIX if ahead_of else ''}")
        click.echo(f"  {ref_content}")
        click.echo(f"  uuid: {new_uuid}")

@cli.command("copy-block", cls=WriteCommand, epilog="""\b
Examples:
  logseq-cli --token TOKEN copy-block --id UUID --to-page "Target Page"
  logseq-cli --token TOKEN copy-block --id UUID --to-page "Target Page" --remove
Note:
  Copies block + all children. With --remove: original is deleted (move).
  The copy gets new UUIDs, so --remove refuses while ((block-refs)) point
  into the original, even from within it; move-block keeps the UUIDs. The
  source's id:: lines are left out of the copy.
  A source block Logseq would not read back as one block (a "- " or "# " line
  after the first, a code fence nothing closes) is refused before anything is
  copied; move-block moves it as it is.
  [graph] keep_empty_blocks_last (or LOGSEQ_CLI_KEEP_EMPTY_BLOCKS_LAST): a write that ends
  a section goes before the empty blocks that end it, so they stay last, and
  nothing is overwritten.
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
    keep_on = keep_empty_blocks_last(load_config())
    # A missing page under the name Logseq creates it with, as for
    # insert-block --page.
    ref, target = follow_page_to_write(api, to_page, as_json)
    names = ref.fields("to_page")
    to_page = target.name
    block_id = block_id.strip("()")
    source = api.get_block(block_id, include_children=True)
    if not source:
        fail("Block not found.", as_json=as_json, id=block_id)

    # The copy is new text, so it must come back from the file as the blocks
    # written, like any write (#47); checked for the whole subtree first.
    refuse_split_tree([source], command="copy-block", label="The source block",
                      single_label="The source block")

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
        content = without_block_ids(source.get("content", "")) if isinstance(source, dict) else ""
        would_anchor = (first_empty_at_end(api, [{"content": content, "children": []}], page_name=to_page)
                      if keep_on else None)
        if as_json:
            output({"action": action, "blocks": planned, **names,
                    "source_id": block_id, "removes_source": bool(remove),
                    "refs_broken": len(refs), "dry_run": True,
                    **would_go_before_fields(would_anchor)}, True)
        else:
            click.echo(f"[DRY RUN] Would {action} {planned} block(s) to '{to_page}'")
            preview = content[:80] + ("..." if len(content) > 80 else "")
            if preview:
                click.echo(f"  root: {preview}")
            if remove:
                click.echo(f"  source block {block_id} WOULD BE REMOVED after copying")
            if refs:
                click.echo(f"  block refs into the source that would dangle: {len(refs)}")
            if would_anchor:
                click.echo(f"  would go before the empty block {would_anchor[:8]}...")
        return

    # Every insert is proven by the API: Logseq answers a failed write with
    # HTTP 200 + null, so an unchecked copy reported "Moved N block(s)" with
    # exit 0 while nothing arrived. With --remove that unverified success would
    # then delete the source, which destroys the block for good.
    def _copy_tree(block, parent_uuid=None):
        # The copy gets uuids of its own, and refs stay with the original, so
        # the source's id:: lines go without a word; left in, the file would
        # name one uuid for two blocks until Logseq reads it again (#56).
        content = without_block_ids(block.get("content", ""))
        if parent_uuid:
            new_uuid = api.insert_block(parent_uuid, content, {"sibling": False})["uuid"]
        else:
            # The root is the one write at the end of the page: before the empty
            # blocks that end it, with [graph] keep_empty_blocks_last on.
            root, ahead_of["uuid"] = before_empty_or_append(
                api, [{"content": content, "children": []}],
                lambda: [api.append_block_in_page(to_page, content)["uuid"]],
                keep_last=keep_on, page_name=to_page)
            new_uuid = root[0]
        copied = 1
        for child in block.get("children", []):
            copied += _copy_tree(child, new_uuid)
        return copied

    ahead_of = {}
    create_missing_page(api, target)
    count = _copy_tree(source)

    if remove:
        # Only reached when every insert above was proven, so the source is
        # removed against a copy that is known to exist, never a claimed one.
        api.remove_block(block_id)

    action = "Moved" if remove else "Copied"
    result_data = {"action": action.lower(), "blocks": count, **names, "source_id": block_id}
    result_data.update(before_empty_fields(ahead_of.get("uuid")))
    if remove:
        result_data["refs_broken"] = len(refs)

    if as_json:
        output(result_data, True)
    else:
        click.echo(f"{action} {count} block(s) to '{to_page}'"
                   f"{BEFORE_EMPTY_SUFFIX if ahead_of.get('uuid') else ''}.")
        if refs:
            click.echo(f"  {len(refs)} block ref(s) into the source now point at nothing")

@cli.command("move-block", cls=WriteCommand, epilog="""\b
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
