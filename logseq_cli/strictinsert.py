"""Strict Insert: every write here lands where it was asked to, or is refused.

Logseq answers some failed writes with HTTP 200 and a ``null`` body, so no
write is trusted on its answer alone: the inserts, batches and moves here are
proven by the LogseqAPI methods that send them (the note above
``api._METHODS`` says how and why). What stays here is what only the caller
knows: where a ``--keep-ids`` tree was sent and under which ids, and whether
a move is possible at all. A tree written with ``--keep-ids`` and a block
moved by move-block are held to the same contract, which is why they live
here too.
"""

import re

import click

from logseq_cli.blocktext import id_lines
from logseq_cli.ids import collect_block_ids
from logseq_cli.outlinetext import (
    collect_child_uuids,
    count_blocks,
    page_blocks_by_uuid,
)
from logseq_cli.writerefused import WriteNotVerified, WriteRefused, partial_state

# Why a --keep-ids write is refused while a block is open (E2, spec 030).
# Not "would discard": Logseq saves the open block before it inserts (M10).
KEPT_IDS_MOVE_CURSOR = "a write with kept ids would move the cursor out of the block being edited"


def _write_one_keeping_id(api, content, where, target):
    """One block through :func:`insert_tree_keeping_ids`, answered as the
    block, the way insertBlock and appendBlockInPage answer."""
    uuid, = insert_tree_keeping_ids(api, [{"content": content, "children": []}],
                                    where, target)
    return api.get_block(uuid, include_children=False) or {"uuid": uuid}


def append_in_page(api, page_name: str, content: str, keep_ids: bool):
    """``appendBlockInPage``, or with ``keep_ids`` the same position through
    :func:`insert_tree_keeping_ids`. Answers the block, as the API does."""
    if not keep_ids:
        return api.append_block_in_page(page_name, content)
    return _write_one_keeping_id(api, content, "page_end", page_name)


def insert_block_at(api, target: str, content: str, *, sibling: bool,
                    before: bool = False, keep_ids: bool = False):
    """``insertBlock`` for one block, or with ``keep_ids`` the same position
    through :func:`insert_tree_keeping_ids`. Answers the block, as the API does.

    insertBlock's positions: not ``sibling`` is the last child (the first with
    ``before``), ``sibling`` right after the target (before it with ``before``).
    """
    if not keep_ids:
        # The options as they have always been sent: a plain child append
        # never carried "before".
        opts = {"sibling": sibling, "before": before} if sibling or before else {"sibling": False}
        return api.insert_block(target, content, opts)
    where = (("before" if before else "after") if sibling
             else ("first_child" if before else "last_child"))
    return _write_one_keeping_id(api, content, where, target)


# The lines insertBatchBlock takes out of a block's content when it is not
# asked to keep uuids (measured, 0.10.15): "id:: " with any value or none, in
# any case, inside a code block too, behind any whitespace JavaScript's \s
# knows (space, tab, form feed, carriage return, no-break space and vertical
# tab measured; \ufeff is in \s there and not in Python's). Not "id::"
# alone, not "custom-id::". insertBlock writes the same text as given.
_BATCH_DROPS_RE = re.compile(r'^[\s\ufeff]*id:: ', re.IGNORECASE)


def _quotes_an_id_line(tree: list) -> bool:
    """Whether a node of ``tree`` has a line the batch would drop that is not
    the block's id: one inside a code block, say. Such a tree goes block by
    block, so the text is written as given."""
    for block in tree or []:
        if not isinstance(block, dict):
            continue
        for line, value in id_lines(block.get("content", "")):
            if value is None and _BATCH_DROPS_RE.match(line):
                return True
        if _quotes_an_id_line(block.get("children") or []):
            return True
    return False


def insert_block_tree_with_uuids(api, tree: list, parent_uuid: str, *, batch: bool = True, keep_ids: bool = False) -> list:
    """Recursively insert a parsed tree under ``parent_uuid``.

    Returns the UUIDs of inserted blocks in DFS pre-order (parent before
    children, siblings in declaration order). A block Logseq does not write
    raises WriteNotVerified from the API; the blocks before it stay.

    ``batch`` (the default) sends a multi-block tree as a single
    ``insertBatchBlock`` call via :func:`insert_block_tree_batched`, which the
    API method proves by re-reading. Set ``batch=False`` to force the
    per-block path when the caller needs a UUID for every node as it is
    written.

    While a block is open in Logseq's editor the tree goes block by block
    too: the batch would open its last block and move the cursor out of the
    one being typed in (E2, spec 030); ``insertBlock`` goes with
    ``focus: false`` and leaves it where it is.
    """
    if keep_ids:
        return insert_tree_keeping_ids(api, tree, "last_child", parent_uuid)
    if batch and count_blocks(tree) > 1 and not _quotes_an_id_line(tree):
        # One round-trip instead of N. NOT atomic: a batch can still write only
        # part of its nodes, and it answers null either way, so the API method
        # proves it by re-reading. Single blocks keep the per-block path, which
        # returns the UUID directly and needs no verifying read.
        if api.check_editing() is None:
            return insert_block_tree_batched(api, tree, parent_uuid)
        # Someone is typing: block by block, the children too, without asking
        # again.
        batch = False

    uuids = []
    for block in tree:
        new_uuid = api.insert_block(parent_uuid, block["content"], {"sibling": False})["uuid"]
        uuids.append(new_uuid)
        children = block.get("children") or []
        if children:
            uuids.extend(insert_block_tree_with_uuids(api, children, new_uuid, batch=batch))
    return uuids


def insert_block_tree_batched(api, tree: list, parent_uuid: str) -> list:
    """Insert a tree as the last children of ``parent_uuid`` in ONE API call.

    ``insertBatchBlock`` replaces N ``insertBlock`` round-trips with one. It is
    NOT atomic: verified against a live graph, a batch containing a malformed
    node writes its siblings and skips that node, so a partial write is still
    possible - one call is fewer chances to fail, not none. It answers ``null``
    whatever it did; LogseqAPI.insert_batch_block proves it by reading the
    place back, which also recovers the UUIDs the call withholds.

    Positioning: with ``sibling: false`` the batch lands at the HEAD of the child
    list (``before: false`` does not change it), so to append we anchor on the
    last existing child with ``sibling: true``. With no children yet, the parent
    itself is the anchor.

    Returns the new UUIDs in DFS pre-order. Raises ``ClickException`` for a
    parent that is not there, before anything is written; the API method
    raises WriteNotVerified for a batch that did not land in full.
    """
    if not tree:
        return []

    before = api.get_block(parent_uuid, include_children=True)
    if not before:
        raise click.ClickException(
            f"Cannot insert: block {parent_uuid[:8]}... not found "
            "(the target UUID does not exist, or the page is not loaded). "
            "Nothing was written."
        )
    existing = [c for c in (before.get("children") or []) if isinstance(c, dict)]

    if existing and existing[-1].get("uuid"):
        anchor, opts = existing[-1]["uuid"], {"sibling": True}
    else:
        anchor, opts = parent_uuid, {"sibling": False}

    return api.insert_batch_block(anchor, tree, opts)


# Where a --keep-ids write lands, and how insertBatchBlock is asked for it
# ------------------------------------------------------------------------
# Every write that keeps ids goes through insertBatchBlock with keepUUID and
# the id as an ``id::`` line: it is the one call that takes over the
# placeholder Logseq keeps for a ((ref)) whose block is gone, where insertBlock
# and appendBlockInPage answer "Custom block UUID already exists" (#31). One
# call for every position, rather than a second path for placeholders only, so
# a restore cannot behave differently from a move depending on which ids the
# graph happens to hold.
#
# The positions, measured on 0.10.15:
#
#   last_child   after the parent's last child (sibling), or under a parent
#                with none (not sibling)
#   first_child  under the parent (not sibling): the batch goes to the head
#   after        after the anchor (sibling)
#   before       before the anchor (sibling + before), except before the
#                page's first block: there Logseq writes every node with "* "
#                in front of its content. The batch goes after that block and
#                its roots are moved before it, which moveBlock does cleanly.
#   page_end     after the page's last top-level block (sibling). On a page
#                with no blocks, the page uuid as anchor has the same "* "
#                defect, so a stand-in block is appended, written after, and
#                removed.
#
# insertBatchBlock answers null whatever it did. LogseqAPI.insert_batch_block
# proves that the blocks arrived, as many as sent and with their texts; the
# page is read back here for what only this caller knows: that they carry the
# ids asked for, and sit where they were sent. Two statements, not one twice.


def _landed_where_sent(where, target, siblings, first, count, parent) -> bool:
    """Whether ``count`` roots starting at ``siblings[first]`` sit at ``where``
    relative to ``target``, in the page tree read back."""
    end = first + count
    if where == "last_child":
        return (parent or {}).get("uuid") == target and end == len(siblings)
    if where == "first_child":
        return (parent or {}).get("uuid") == target and first == 0
    if where == "after":
        return first > 0 and siblings[first - 1]["uuid"] == target
    if where == "before":
        return end < len(siblings) and siblings[end]["uuid"] == target
    return parent is None and end == len(siblings)  # page_end


def insert_tree_keeping_ids(api, tree: list, where: str, target: str) -> list:
    """Write ``tree`` at ``where`` relative to ``target``, keeping its ids.

    ``where`` is one of the positions in the note above; ``target`` is a block
    uuid, or for ``page_end`` a page name. The ids have been checked by
    :func:`check_block_ids` before: insertBatchBlock would give a real
    block's uuid to a second block without a word (measured).

    Returns the new uuids in DFS pre-order. Raises ``ClickException`` when the
    target is missing, before anything is written, and WriteNotVerified when
    the blocks landed under other ids or elsewhere; what landed earlier in the
    call, the error handler reports from ``api.writes_landed``.
    """
    if not tree:
        return []
    # Before the first write, the page made for page_end included: a kept id
    # goes only through the batch (#31), and the batch would move the cursor
    # out of the block being typed in (E2, spec 030).
    editing = api.check_editing()
    if editing is not None:
        api.refuse_open(editing, why=KEPT_IDS_MOVE_CURSOR)
    if where == "page_end":
        page_name = target
        if api.get_page(page_name) is None:
            # appendBlockInPage creates a missing page and writes to it; this
            # position does the same, without the empty block Logseq would
            # otherwise put first (M18). The empty page takes the stand-in below.
            api.create_page(page_name, first_block=False)
    else:
        # Logseq's uuids are lower-case; a target typed in capitals is the
        # same block.
        target = target.lower()
        block = api.get_block(target, include_children=False)
        page = api.get_page((block or {}).get("page", {}).get("id")) if block else None
        if not page:
            raise click.ClickException(
                f"Cannot insert: block {target[:8]}... not found (the uuid does not "
                f"exist, or its page is not loaded). {partial_state(api.writes_landed)}")
        page_name = page.get("originalName") or page.get("name")

    before_tree = api.get_page_blocks_tree(page_name) or []
    index = page_blocks_by_uuid(before_tree)
    if where != "page_end" and target not in index:
        raise click.ClickException(
            f"Cannot insert: block {target[:8]}... is not on '{page_name}' as read. "
            f"{partial_state(api.writes_landed)}")

    opts = {"keepUUID": True}
    stand_in = None
    lead_first = False
    if where == "last_child":
        kids = index[target][0][index[target][1]].get("children") or []
        anchor = kids[-1]["uuid"] if kids else target
        opts["sibling"] = bool(kids)
    elif where == "first_child":
        anchor, opts["sibling"] = target, False
    elif where == "after":
        anchor, opts["sibling"] = target, True
    elif where == "before":
        siblings, i, parent = index[target]
        lead_first = parent is None and i == 0
        anchor, opts["sibling"] = target, True
        if not lead_first:
            opts["before"] = True
    elif where == "page_end":
        if before_tree:
            anchor, opts["sibling"] = before_tree[-1]["uuid"], True
        else:
            stand_in = api.append_block_in_page(page_name, "")["uuid"]
            anchor, opts["sibling"] = stand_in, True
    else:
        raise ValueError(f"unknown position {where!r}")

    try:
        new = api.insert_batch_block(anchor, tree, opts)
    except Exception as failed:
        if stand_in:
            _remove_stand_in(api, stand_in, failed)
        raise
    if stand_in:
        # The batch landed: a stand-in left behind is an error of its own.
        api.remove_block(stand_in)

    expected = count_blocks(tree)
    after_tree = api.get_page_blocks_tree(page_name) or []
    missing = [i for i in collect_block_ids(tree) if i.lower() not in new]
    if missing:
        shown = f"{', '.join(missing[:3])}{' ...' if len(missing) > 3 else ''}"
        raise WriteNotVerified(
            f"{expected} block(s) were written on '{page_name}', but not under "
            f"the ids asked for: {shown}. Logseq minted new ones; the "
            "((refs)) to these ids still point nowhere. Check the page.",
            method="insertBatchBlock", target=target,
            expected=f"the ids {shown}", got="new ids")

    sent = f"{where.replace('_', ' ')} {target if where == 'page_end' else target[:8] + '...'}"

    def elsewhere():
        return WriteNotVerified(
            f"{expected} block(s) were written on '{page_name}' with their ids, but "
            f"not where they were sent ({sent}). "
            "Check the page; move-block puts them in place and keeps their ids.",
            method="insertBatchBlock", target=target, expected=sent, got="elsewhere")

    index = page_blocks_by_uuid(after_tree)
    if any(u not in index for u in new):
        # The method read them where the batch was sent; not on this page now.
        raise elsewhere()
    roots = [u for u in new if index[u][2] is None or index[u][2]["uuid"] not in new]
    if lead_first:
        # Written after the first block; the roots go before it, in order.
        # ``new`` keeps its order: only that block moved relative to them.
        for root in roots:
            api.move_block(root, target, {"before": True})
        after_tree = api.get_page_blocks_tree(page_name) or []
        index = page_blocks_by_uuid(after_tree)

    siblings, first, parent = index[roots[0]]
    run = [b["uuid"] for b in siblings[first:first + len(roots)]]
    if run != roots or not _landed_where_sent(where, target, siblings, first,
                                              len(roots), parent):
        raise elsewhere()
    return new


def _remove_stand_in(api, stand_in: str, failed: Exception) -> None:
    """Remove the stand-in after the batch raised ``failed``; a refusal of
    the removal is added to ``failed``'s message rather than raised over it,
    which would hide why the batch failed. Any WriteRefused: the removal can
    meet an error object, an open editor, or a removal that did not show."""
    try:
        api.remove_block(stand_in)
    except WriteRefused as refused:
        note = (f"The empty stand-in block {stand_in[:8]}... written for the batch "
                f"stayed too: {refused}")
        if isinstance(failed, click.ClickException):
            failed.message = f"{failed.message} {note}"
        elif failed.args:
            failed.args = (f"{failed.args[0]} {note}", *failed.args[1:])


def check_move(api, src_uuid: str, target_uuid: str) -> None:
    """Refuse a move Logseq would not carry out, before anything is written.

    Shared by the move and its dry run, so a preview cannot promise a move the
    real run refuses. The subtree case is the one refusal Logseq is known for,
    and it gives it by doing nothing, so this is the only place it can be named.
    """
    src_uuid = src_uuid.strip().replace("((", "").replace("))", "")
    target_uuid = target_uuid.strip().replace("((", "").replace("))", "")
    if src_uuid == target_uuid:
        raise click.ClickException("Source and target are the same block.")

    source = api.get_block(src_uuid, include_children=True)
    if not source:
        raise click.ClickException(
            f"Source block {src_uuid[:8]}... not found. Nothing was moved.")
    if not api.get_block(target_uuid, include_children=False):
        raise click.ClickException(
            f"Target block {target_uuid[:8]}... not found. Nothing was moved.")
    if target_uuid in collect_child_uuids(source):
        raise click.ClickException(
            f"Target {target_uuid[:8]}... lies inside the subtree of "
            f"{src_uuid[:8]}...; a block cannot be moved into its own subtree. "
            "Nothing was moved.")


def move_block_verified(api, src_uuid: str, target_uuid: str, *, before: bool = False) -> None:
    """Move ``src_uuid`` to ``target_uuid``, refusing up front what Logseq
    would not do; LogseqAPI.move_block proves that it landed.

    Unlike copy+remove, the block keeps its UUID, so every ``((block-ref))``
    pointing at it survives the move.

    Position follows what ``moveBlock`` actually does, which is narrower than
    its option names suggest (probed against a live graph): ``before: true``
    places the block as the sibling *before* the target; everything else,
    including the ``sibling: true`` the option list implies, nests it as the
    target's first child. There is no "sibling after" - anchor on the following
    block with ``before`` instead.

    ``moveBlock`` answers ``null`` for a successful move, a non-existent target
    AND a refused one (Logseq declines to move a block into its own subtree, and
    says so only by doing nothing), so the response proves nothing. The subtree
    case is therefore refused here before the call, where it can be named; the
    method reads the block back where it was sent.
    """
    src_uuid = src_uuid.strip().replace("((", "").replace("))", "")
    target_uuid = target_uuid.strip().replace("((", "").replace("))", "")
    check_move(api, src_uuid, target_uuid)
    api.move_block(src_uuid, target_uuid, {"before": True} if before else {"children": True})


def insert_block_tree_as_first_children(api, tree: list, parent_uuid: str, *, keep_ids: bool = False) -> list:
    """Insert a parsed tree at the HEAD of ``parent_uuid``'s child list.

    ``insertBlock`` can address the first child position (``sibling: false`` +
    ``before: true``) but has no "nth child" option. Looping over the roots with
    ``before=True`` would therefore push each one ahead of the previous and
    reverse the declaration order. So the first root claims the head position
    and the remaining roots chain as siblings behind it, which preserves the
    order the caller wrote.

    Returns the UUIDs in DFS pre-order, like the sibling/child variants.
    """
    if not tree:
        return []
    if keep_ids:
        return insert_tree_keeping_ids(api, tree, "first_child", parent_uuid)
    head_uuid = api.insert_block(parent_uuid, tree[0]["content"],
                                 {"sibling": False, "before": True})["uuid"]
    uuids = [head_uuid]
    children = tree[0].get("children") or []
    if children:
        uuids.extend(insert_block_tree_with_uuids(api, children, head_uuid))
    if len(tree) > 1:
        uuids.extend(insert_block_tree_as_siblings(api, tree[1:], head_uuid))
    return uuids


def insert_block_tree_as_siblings(api, tree: list, anchor_uuid: str, *, before: bool = False, keep_ids: bool = False) -> list:
    """Insert a parsed tree as sibling(s) after (or before) ``anchor_uuid``.

    The first top-level node is inserted as a sibling of the anchor; its
    children are nested beneath it; each further top-level node is inserted as
    a sibling after the previous top-level node, preserving declaration order.
    This is the ``--after``/``--before`` counterpart to
    :func:`insert_block_tree_with_uuids` (which only nests under a parent).

    Returns inserted UUIDs in DFS pre-order.
    """
    if keep_ids:
        return insert_tree_keeping_ids(api, tree, "before" if before else "after",
                                       anchor_uuid)
    uuids = []
    cursor = anchor_uuid
    for position, block in enumerate(tree):
        # Only the first root goes before the anchor; each further one after
        # the root just written. Sending every root "before" the one written
        # ahead of it put a, b, c down as c, b, a (measured, 0.10.15).
        ahead = before and position == 0
        new_uuid = api.insert_block(cursor, block["content"],
                                    {"sibling": True, "before": ahead})["uuid"]
        uuids.append(new_uuid)
        children = block.get("children") or []
        if children:
            uuids.extend(insert_block_tree_with_uuids(api, children, new_uuid))
        cursor = new_uuid
    return uuids


def insert_block_tree_at_page_top(api, tree: list, page_name: str, *, keep_ids: bool = False) -> list:
    """Insert tree starting at the top of ``page_name``.

    Top-level nodes use ``append_block_in_page`` (which currently appends; the
    Logseq API has no first-block primitive). Children use insert_block.
    Returns DFS pre-order UUIDs. A failed append raises WriteNotVerified from
    the API, so the caller cannot report success for text that was never
    written.
    """
    if keep_ids:
        return insert_tree_keeping_ids(api, tree, "page_end", page_name)
    uuids = []
    for block in tree:
        new_uuid = api.append_block_in_page(page_name, block["content"])["uuid"]
        uuids.append(new_uuid)
        children = block.get("children") or []
        if children:
            uuids.extend(insert_block_tree_with_uuids(api, children, new_uuid))
    return uuids


def insert_tree_at_page_end(api, page_name: str, tree: list, *, keep_ids: bool = False) -> list:
    """Append a parsed tree to a page, returning the inserted block UUIDs.

    It takes the tree, not the text: the caller checked and cleaned that tree
    for ``id::`` lines, and parsing the text again here would write something
    the check never saw.

    Top-level nodes are appended to the page; children use insert_block.
    Returns UUIDs in DFS pre-order (parent before children).

    A page Logseq has not loaded answers every append with HTTP 200 +
    ``null``; the API raises WriteNotVerified on the first, where once the
    ``None`` UUIDs were counted and the caller reported "Added N block(s)"
    with exit 0 for a journal entry that was never written.
    """
    if keep_ids:
        return insert_tree_keeping_ids(api, tree, "page_end", page_name)
    uuids = []

    def insert_tree(blocks, parent_uuid=None):
        for block in blocks:
            if parent_uuid:
                result = api.insert_block(parent_uuid, block["content"], {"sibling": False})
            else:
                result = api.append_block_in_page(page_name, block["content"])
            new_uuid = result["uuid"]
            uuids.append(new_uuid)
            if block["children"]:
                insert_tree(block["children"], new_uuid)

    insert_tree(tree)
    return uuids
