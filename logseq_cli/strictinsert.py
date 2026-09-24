"""Strict Insert: every write here lands where it was asked to, or is refused.

Logseq answers some failed writes with HTTP 200 and a ``null`` body, so no
write here is trusted on its answer alone: a returned uuid is required, and
where Logseq returns nothing the blocks are read back. The note above
require_insert says which calls need the read and why. A tree written with
``--keep-ids`` and a block moved by move-block are held to the same contract,
which is why they live here too.
"""

import re

import click

from logseq_cli.blocktext import id_lines
from logseq_cli.ids import collect_block_ids
from logseq_cli.outlinetext import count_blocks


def _write_one_keeping_id(api, content, where, target, written_before):
    """One block through :func:`insert_tree_keeping_ids`, answered as the
    block, the way insertBlock and appendBlockInPage answer."""
    uuid, = insert_tree_keeping_ids(api, [{"content": content, "children": []}],
                                    where, target, written_before=written_before)
    return api.get_block(uuid, include_children=False) or {"uuid": uuid}


def append_in_page(api, page_name: str, content: str, keep_ids: bool, *,
                   written_before: int = 0):
    """``appendBlockInPage``, or with ``keep_ids`` the same position through
    :func:`insert_tree_keeping_ids`. Answers the block, as the API does."""
    if not keep_ids:
        return api.append_block_in_page(page_name, content)
    return _write_one_keeping_id(api, content, "page_end", page_name, written_before)


def insert_block_at(api, target: str, content: str, *, sibling: bool,
                    before: bool = False, keep_ids: bool = False,
                    written_before: int = 0):
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
    return _write_one_keeping_id(api, content, where, target, written_before)


def block_uuid_from_result(result):
    """Extract a block UUID from a Logseq insert/append API result.

    The API returns a block map ``{"uuid": "...", ...}`` on success, a bare
    UUID string in some paths, or ``None`` when the operation silently failed
    (e.g. an unknown anchor UUID — the API answers HTTP 200 with ``null``).
    Returns the UUID string or ``None``.
    """
    if isinstance(result, dict):
        return result.get("uuid")
    if isinstance(result, str):
        return result
    return None


# Why some writes are verified by reading them back
# ---------------------------------------------------
# Three API methods answer ``null`` for a successful call as well as a failed
# one, so their return value carries no success signal at all (each verified
# against a live graph, 2026-08-22):
#
#   insertBatchBlock  null on success, on partial write, and on failure
#   moveBlock         null on success, on a missing target, and on a refusal
#                     (Logseq declines to move a block into its own subtree)
#   updateBlock       null on success and on a non-existent block UUID
#
# For these, :func:`require_insert` cannot help: there is no UUID to miss. The
# callers therefore re-read the affected blocks and compare against what they
# intended to write. That costs one extra API call per operation and is a
# workaround, not a design choice.
#
# If a future Logseq version returns the written block (or any error signal) for
# these methods, drop the verifying read and route them through
# ``require_insert`` like every other write, keeping the read only where a count
# has to be compared. The affected call sites are
# :func:`insert_block_tree_batched`, :func:`move_block_verified` and the
# ``replace-text`` command; they are the ones to revisit.
#
# ``insertBlock`` and ``appendBlockInPage`` do return the new block, which is
# why ``require_insert`` works for them and is the cheaper check to prefer.


def require_insert(result, what: str, *, written_so_far: int = 0) -> str:
    """Return the UUID of a just-inserted block, or abort loudly.

    The Logseq API answers a failed insert/append with HTTP 200 + ``null``
    instead of an error status, so a missing UUID is the only failure signal.
    Callers that must not continue on a silent write failure use this to turn
    that ``null`` into a non-zero exit with a clear message, rather than
    reporting a phantom success.

    ``written_so_far`` is the number of blocks already persisted in this
    operation. There is no rollback (the API offers none), so on a multi-block
    insert those blocks stay. Saying "Nothing was written" there would be a
    lie that invites a retry and thus duplicates, so the message names the
    partial state instead.
    """
    uuid = block_uuid_from_result(result)
    if not uuid:
        if written_so_far:
            tail = (
                f"{written_so_far} block(s) were already written and remain "
                "(no rollback available) — check the page before retrying, or "
                "the retry will duplicate them."
            )
        else:
            tail = "Nothing was written."
        raise click.ClickException(
            f"Logseq did not create {what} (API returned no block UUID). "
            "Likely cause: the target/anchor UUID does not exist, or the page "
            f"is not loaded. {tail}"
        )
    return uuid


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


def insert_block_tree_with_uuids(api, tree: list, parent_uuid: str, *, strict: bool = True, batch: bool = True, keep_ids: bool = False, _written: int = 0) -> list:
    """Recursively insert a parsed tree under ``parent_uuid``.

    Returns the UUIDs of inserted blocks in DFS pre-order (parent before
    children, siblings in declaration order).

    ``strict`` (the default) turns a silent write failure into a hard abort via
    :func:`require_insert`. Logseq answers a failed insert with HTTP 200 +
    ``null``, so without this the function pushes a ``None`` UUID, skips that
    block's children, and the caller reports success for content that was never
    written — the worst outcome for a journal entry, since the text is gone and
    nothing says so. ``strict=False`` is only for callers that deliberately
    tolerate partial writes; it must never be the default.

    ``batch`` (the default) sends a multi-block tree as a single
    ``insertBatchBlock`` call via :func:`insert_block_tree_batched`, which
    verifies the write by re-reading. It requires ``strict``, because the
    non-strict contract is to return a ``None`` per unwritten block, and the
    batch path cannot say which nodes those were: the API reports neither an
    error nor UUIDs. Set ``batch=False`` to force the per-block path when the
    caller needs a UUID for every node as it is written.
    """
    if keep_ids:
        return insert_tree_keeping_ids(api, tree, "last_child", parent_uuid,
                                       written_before=_written)
    if strict and batch and count_blocks(tree) > 1 and not _quotes_an_id_line(tree):
        # One round-trip instead of N. NOT atomic: a batch can still write only
        # part of its nodes (verified against a live graph - a malformed node is
        # skipped while its siblings land), and it answers null either way. That
        # is why the batch path verifies by re-reading instead of trusting the
        # response. Single blocks keep the per-block path, which returns the
        # UUID directly and needs no verifying read.
        return insert_block_tree_batched(api, tree, parent_uuid)

    uuids = []
    for block in tree:
        result = api.insert_block(parent_uuid, block["content"], {"sibling": False})
        if strict:
            new_uuid = require_insert(result, "a block", written_so_far=_written + len(uuids))
        else:
            new_uuid = block_uuid_from_result(result)
        uuids.append(new_uuid)
        children = block.get("children") or []
        if new_uuid and children:
            uuids.extend(insert_block_tree_with_uuids(
                api, children, new_uuid, strict=strict,
                _written=_written + len(uuids)))
    return uuids


def _collect_child_uuids(node) -> list:
    """UUIDs of a getBlock(includeChildren=True) subtree, DFS pre-order."""
    out = []
    for child in (node.get("children") or []):
        if not isinstance(child, dict):
            continue  # a children list of bare UUID refs carries no content
        if child.get("uuid"):
            out.append(child["uuid"])
        out.extend(_collect_child_uuids(child))
    return out


def insert_block_tree_batched(api, tree: list, parent_uuid: str) -> list:
    """Insert a tree under ``parent_uuid`` in ONE API call, then verify.

    ``insertBatchBlock`` replaces N ``insertBlock`` round-trips with one. It is
    NOT atomic: verified against a live graph, a batch containing a malformed
    node writes its siblings and skips that node, so a partial write is still
    possible - one call is fewer chances to fail, not none.

    Worse, the API answers ``null`` whether it wrote everything, part of it, or
    nothing, so the return value carries no success signal at all. That is
    exactly the silent-write-failure shape this codebase refuses to accept, so
    the write is proven instead: the parent's children are read back and the new
    UUIDs counted. The read also recovers the UUIDs the batch call withholds.
    See the note above :func:`require_insert` for when this read can be dropped.

    Positioning: with ``sibling: false`` the batch lands at the HEAD of the child
    list (``before: false`` does not change it), so to append we anchor on the
    last existing child with ``sibling: true``. With no children yet, the parent
    itself is the anchor.

    Returns the new UUIDs in DFS pre-order. Raises ``ClickException`` if the
    graph does not show the expected number of new blocks afterwards.
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
    before_uuids = set(_collect_child_uuids(before))

    if existing and existing[-1].get("uuid"):
        anchor, opts = existing[-1]["uuid"], {"sibling": True}
    else:
        anchor, opts = parent_uuid, {"sibling": False}

    api.insert_batch_block(anchor, tree, opts)

    after = api.get_block(parent_uuid, include_children=True)
    after_uuids = _collect_child_uuids(after) if after else []
    new = [u for u in after_uuids if u not in before_uuids]

    expected = count_blocks(tree)
    if len(new) != expected:
        raise click.ClickException(
            f"Batch insert wrote {len(new)} of {expected} block(s) under "
            f"{parent_uuid[:8]}... . The API reports no error for this, so the "
            "graph was re-read to check. Verify the page before retrying, or the "
            "retry will duplicate what did land."
        )
    return new


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
# insertBatchBlock answers null whatever it did, so the page is read back:
# the new blocks must be there in the number sent, carry the ids asked for,
# and sit where they were sent.


def _page_blocks_by_uuid(tree: list) -> dict:
    """``uuid -> (siblings, index, parent block or None)`` over a page tree."""
    found = {}

    def walk(blocks, parent):
        for i, block in enumerate(blocks):
            found[block["uuid"]] = (blocks, i, parent)
            walk(block.get("children") or [], block)
    walk(tree, None)
    return found


def _preorder_uuids(tree: list) -> list:
    out = []
    for block in tree:
        out.append(block["uuid"])
        out.extend(_preorder_uuids(block.get("children") or []))
    return out


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


def insert_tree_keeping_ids(api, tree: list, where: str, target: str, *,
                            written_before: int = 0) -> list:
    """Write ``tree`` at ``where`` relative to ``target``, keeping its ids.

    ``where`` is one of the positions in the note above; ``target`` is a block
    uuid, or for ``page_end`` a page name. The ids have been checked by
    :func:`check_block_ids` before: insertBatchBlock would give a real
    block's uuid to a second block without a word (measured).
    ``written_before`` counts blocks this command already wrote, for the
    message when this write fails.

    Returns the new uuids in DFS pre-order. Raises ``ClickException`` when the
    target is missing or the read-back disagrees with what was sent.
    """
    if not tree:
        return []
    earlier = (f" {written_before} block(s) written earlier by this command "
               "remain." if written_before else "")
    if where == "page_end":
        page_name = target
        if api.get_page(page_name) is None:
            # appendBlockInPage creates a missing page (with its empty block)
            # and writes after it (measured); this position does the same.
            api.create_page(page_name)
    else:
        # Logseq's uuids are lower-case; a target typed in capitals is the
        # same block.
        target = target.lower()
        block = api.get_block(target, include_children=False)
        page = api.get_page((block or {}).get("page", {}).get("id")) if block else None
        if not page:
            raise click.ClickException(
                f"Cannot insert: block {target[:8]}... not found (the uuid does not "
                f"exist, or its page is not loaded). Nothing more was written.{earlier}")
        page_name = page.get("originalName") or page.get("name")

    before_tree = api.get_page_blocks_tree(page_name) or []
    index = _page_blocks_by_uuid(before_tree)
    if where != "page_end" and target not in index:
        raise click.ClickException(
            f"Cannot insert: block {target[:8]}... is not on '{page_name}' as read. "
            f"Nothing more was written.{earlier}")

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
            stand_in = require_insert(api.append_block_in_page(page_name, ""),
                                      f"a block on '{page_name}'")
            anchor, opts["sibling"] = stand_in, True
    else:
        raise ValueError(f"unknown position {where!r}")

    try:
        api.insert_batch_block(anchor, tree, opts)
    finally:
        if stand_in:
            api.remove_block(stand_in)

    expected = count_blocks(tree)
    old = set(_preorder_uuids(before_tree))
    after_tree = api.get_page_blocks_tree(page_name) or []
    new = [u for u in _preorder_uuids(after_tree) if u not in old and u != stand_in]
    if len(new) != expected:
        raise click.ClickException(
            f"Batch insert wrote {len(new)} of {expected} block(s) on '{page_name}'. "
            "The API reports no error for this, so the page was re-read to check. "
            "Verify the page before retrying, or the retry will duplicate what "
            f"did land.{earlier}")
    missing = [i for i in collect_block_ids(tree) if i.lower() not in new]
    if missing:
        raise click.ClickException(
            f"{expected} block(s) were written on '{page_name}', but not under "
            f"the ids asked for: {', '.join(missing[:3])}"
            f"{' ...' if len(missing) > 3 else ''}. Logseq minted new ones; the "
            f"((refs)) to these ids still point nowhere. Check the page.{earlier}")

    index = _page_blocks_by_uuid(after_tree)
    roots = [u for u in new if index[u][2] is None or index[u][2]["uuid"] not in new]
    if lead_first:
        # Written after the first block; the roots go before it, in order.
        # ``new`` keeps its order: only that block moved relative to them.
        for root in roots:
            api.move_block(root, target, {"before": True})
        after_tree = api.get_page_blocks_tree(page_name) or []
        index = _page_blocks_by_uuid(after_tree)

    siblings, first, parent = index[roots[0]]
    run = [b["uuid"] for b in siblings[first:first + len(roots)]]
    if run != roots or not _landed_where_sent(where, target, siblings, first,
                                              len(roots), parent):
        raise click.ClickException(
            f"{expected} block(s) were written on '{page_name}' with their ids, but "
            f"not where they were sent ({where.replace('_', ' ')} "
            f"{target if where == 'page_end' else target[:8] + '...'}). "
            f"Check the page; move-block puts them in place and keeps their ids.{earlier}")
    return new


def _sibling_uuids_in_order(api, block: dict) -> list:
    """UUIDs of ``block`` and its siblings, in order.

    ``getBlock`` reports a parent as ``{"id": <int>}`` with no UUID, but it also
    accepts that id as its argument, so a nested block's sibling list is
    reachable in one read without walking the page tree.

    That does not hold at the top level. There the parent is the page, and
    ``getBlock`` answers ``null`` for a page id (measured, 0.10.15), so the
    order comes from the page tree. ``getPageBlocksTree`` in turn refuses a
    numeric id ("Expected string, got: number") and needs the page name first.
    """
    parent_id = (block.get("parent") or {}).get("id")
    if parent_id is None:
        return []
    if parent_id == (block.get("page") or {}).get("id"):
        page = api.get_page(parent_id) or {}
        children = api.get_page_blocks_tree(page["name"]) if page.get("name") else None
    else:
        children = (api.get_block(parent_id, include_children=True) or {}).get("children")
    if not isinstance(children, list):
        return []
    return [c.get("uuid") for c in children if isinstance(c, dict) and c.get("uuid")]


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
    if target_uuid in _collect_child_uuids(source):
        raise click.ClickException(
            f"Target {target_uuid[:8]}... lies inside the subtree of "
            f"{src_uuid[:8]}...; a block cannot be moved into its own subtree. "
            "Nothing was moved.")


def move_block_verified(api, src_uuid: str, target_uuid: str, *, before: bool = False) -> None:
    """Move ``src_uuid`` to ``target_uuid``, then prove it landed.

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
    case is therefore refused here before the call, where it can be named, and
    the move itself is verified by re-reading: for a child move the block must
    appear among the target's children, for ``before`` directly in front of the
    target. See the note above :func:`require_insert` for when this read can be
    dropped.
    """
    src_uuid = src_uuid.strip().replace("((", "").replace("))", "")
    target_uuid = target_uuid.strip().replace("((", "").replace("))", "")
    check_move(api, src_uuid, target_uuid)
    api.move_block(src_uuid, target_uuid, {"before": True} if before else {"children": True})

    landed = api.get_block(target_uuid, include_children=True) or {}
    if before:
        # The block must sit directly in front of the target under the same
        # parent. Checking only "same parent" would pass a move that did
        # nothing, since source and target often already share one.
        siblings = _sibling_uuids_in_order(api, landed)
        try:
            ok = siblings.index(src_uuid) + 1 == siblings.index(target_uuid)
        except ValueError:
            ok = False
    else:
        ok = any(
            isinstance(c, dict) and c.get("uuid") == src_uuid
            for c in (landed.get("children") or [])
        )
    if not ok:
        raise click.ClickException(
            f"Move of {src_uuid[:8]}... did not take effect. Logseq reports no error "
            "for this, so the graph was re-read to check; the block is not where it "
            "was sent. Nothing was removed."
        )


def insert_block_tree_as_first_children(api, tree: list, parent_uuid: str, *, keep_ids: bool = False, _written: int = 0) -> list:
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
        return insert_tree_keeping_ids(api, tree, "first_child", parent_uuid,
                                       written_before=_written)
    head = api.insert_block(parent_uuid, tree[0]["content"], {"sibling": False, "before": True})
    head_uuid = require_insert(head, "the first child", written_so_far=_written)
    uuids = [head_uuid]
    children = tree[0].get("children") or []
    if children:
        uuids.extend(insert_block_tree_with_uuids(
            api, children, head_uuid, strict=True, _written=_written + len(uuids)))
    if len(tree) > 1:
        uuids.extend(insert_block_tree_as_siblings(
            api, tree[1:], head_uuid, _written=_written + len(uuids)))
    return uuids


def insert_block_tree_as_siblings(api, tree: list, anchor_uuid: str, *, before: bool = False, strict: bool = True, keep_ids: bool = False, _written: int = 0) -> list:
    """Insert a parsed tree as sibling(s) after (or before) ``anchor_uuid``.

    The first top-level node is inserted as a sibling of the anchor; its
    children are nested beneath it; each further top-level node is inserted as
    a sibling after the previous top-level node, preserving declaration order.
    This is the ``--after``/``--before`` counterpart to
    :func:`insert_block_tree_with_uuids` (which only nests under a parent).

    Returns inserted UUIDs in DFS pre-order. ``strict`` (default True) aborts
    on a silent write failure rather than orphaning the remaining nodes.
    """
    if keep_ids:
        return insert_tree_keeping_ids(api, tree, "before" if before else "after",
                                       anchor_uuid, written_before=_written)
    uuids = []
    cursor = anchor_uuid
    for position, block in enumerate(tree):
        # Only the first root goes before the anchor; each further one after
        # the root just written. Sending every root "before" the one written
        # ahead of it put a, b, c down as c, b, a (measured, 0.10.15).
        ahead = before and position == 0
        result = api.insert_block(cursor, block["content"], {"sibling": True, "before": ahead})
        if strict:
            new_uuid = require_insert(result, "a block", written_so_far=_written + len(uuids))
        else:
            new_uuid = block_uuid_from_result(result)
        uuids.append(new_uuid)
        if not new_uuid:
            # non-strict and the insert failed: stop walking this chain
            break
        children = block.get("children") or []
        if children:
            uuids.extend(insert_block_tree_with_uuids(
                api, children, new_uuid, strict=strict,
                _written=_written + len(uuids)))
        cursor = new_uuid
    return uuids


def insert_block_tree_at_page_top(api, tree: list, page_name: str, *, keep_ids: bool = False, _written: int = 0) -> list:
    """Insert tree starting at the top of ``page_name``.

    Top-level nodes use ``append_block_in_page`` (which currently appends; the
    Logseq API has no first-block primitive). Children use insert_block.
    Returns DFS pre-order UUIDs.

    A failed append answers HTTP 200 + ``null``; :func:`require_insert` turns
    that into a hard abort so the caller cannot report success for text that
    was never written.
    """
    if keep_ids:
        return insert_tree_keeping_ids(api, tree, "page_end", page_name,
                                       written_before=_written)
    uuids = []
    for block in tree:
        result = api.append_block_in_page(page_name, block["content"])
        new_uuid = require_insert(
            result, f"a block on '{page_name}'", written_so_far=_written + len(uuids))
        uuids.append(new_uuid)
        children = block.get("children") or []
        if children:
            uuids.extend(insert_block_tree_with_uuids(
                api, children, new_uuid, _written=_written + len(uuids)))
    return uuids


def insert_tree_at_page_end(api, page_name: str, tree: list, *, strict: bool = True, keep_ids: bool = False) -> list:
    """Append a parsed tree to a page, returning the inserted block UUIDs.

    It takes the tree, not the text: the caller checked and cleaned that tree
    for ``id::`` lines, and parsing the text again here would write something
    the check never saw.

    Top-level nodes are appended to the page; children use insert_block.
    Returns UUIDs in DFS pre-order (parent before children).

    ``strict`` (the default) aborts on a silent write failure, the same
    contract the other tree inserters follow. Without it this was the one
    remaining path where a page that Logseq has not loaded answers every
    append with HTTP 200 + ``null``, the ``None`` UUIDs still get counted, and
    the caller reports "Added N block(s)" with exit 0 for a journal entry that
    was never written.
    """
    if keep_ids:
        return insert_tree_keeping_ids(api, tree, "page_end", page_name)
    uuids = []

    def insert_tree(blocks, parent_uuid=None):
        for block in blocks:
            if parent_uuid:
                result = api.insert_block(parent_uuid, block["content"], {"sibling": False})
                what = "a block"
            else:
                result = api.append_block_in_page(page_name, block["content"])
                what = f"a block on '{page_name}'"
            if strict:
                new_uuid = require_insert(result, what, written_so_far=len(uuids))
            else:
                new_uuid = block_uuid_from_result(result)
            uuids.append(new_uuid)
            if new_uuid and block["children"]:
                insert_tree(block["children"], new_uuid)

    insert_tree(tree)
    return uuids


def subtree_uuids(block: dict) -> list:
    """The block's own UUID and those of all its descendants."""
    return [block["uuid"], *_collect_child_uuids(block)] if block.get("uuid") else []
