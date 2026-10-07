"""Does the block still match what the caller read? One question, one module.

A caller that reads a block, decides and writes can be overtaken: the block
changed in between. ``--expect-hash`` lets it say which state it decided on;
:func:`check_precondition` refuses the write when the block is another. The
refusal never carries the current hash (see ``PreconditionFailed``).

Takes the API, because a check is only worth anything on a read that went
past the cache: :func:`read_fresh` is that read.
"""
from dataclasses import dataclass

from logseq_cli.blocktext import block_hash, tree_hash
from logseq_cli.tasks import ORDER
from logseq_cli.writerefused import BlockNotFound, PreconditionFailed

# The --expect-hash help the commands that change a read block share, so a
# change to what the check promises is made once.
EXPECT_HASH_HELP = (
    "Refuse, without writing, unless the block is still the one this hash was read "
    "from (the hash field of get-block --json). Checked on a read past the cache, "
    "before --dry-run and before the first write.")


@dataclass(frozen=True)
class Expect:
    """What the caller says the block is. An empty one checks nothing."""
    hash: str = None        # --expect-hash
    tree_hash: str = None   # --expect-tree-hash (remove-block: the block and everything under it)
    marker: str = None      # --expect-marker, as parse_marker made it ("NONE" = no marker)

    def __bool__(self) -> bool:
        return self.hash is not None or self.tree_hash is not None or self.marker is not None


NO_MARKER = "NONE"


def parse_marker(value: str) -> str:
    """The marker an ``--expect-marker`` value names, upper case; ``none`` is "NONE".

    Raises ValueError, listing the accepted values, for anything outside
    ``tasks.ORDER``: a typo must not read as a marker no block has.
    """
    wanted = value.strip().upper()
    if wanted != NO_MARKER and wanted not in ORDER:
        raise ValueError(f"unknown marker '{value}'. Use one of {', '.join(ORDER)}, "
                         "or none for a block without a marker.")
    return wanted


def read_fresh(api, block_id: str, *, include_children: bool = False) -> dict:
    """The block as Logseq holds it now, past the cache.

    Raises BlockNotFound when there is none (or only the placeholder Logseq
    keeps for a ref whose block does not exist, see ``api.block_or_none``).
    """
    block = api.get_block(block_id, include_children=include_children, cached=False)
    if not block:
        raise BlockNotFound(f"Block {block_id} not found.", id=block_id)
    return block


def check_precondition(block: dict, expect: Expect) -> None:
    """Raise PreconditionFailed unless ``block`` still matches all of ``expect``.

    ``block`` is the caller's own read of the target, taken past the cache
    (:func:`read_fresh`), the one it builds the new content from. Does nothing
    for an empty ``expect``.
    """
    if not expect:
        return
    marker = block.get("marker") or None
    hash_differs = (expect.hash is not None
                    and block_hash(block.get("content")) != expect.hash.strip().lower())
    # Needs the children as blocks: the caller read them (read_fresh(include_children=True)).
    tree_differs = (expect.tree_hash is not None
                    and tree_hash(block) != expect.tree_hash.strip().lower())
    # Logseq's own field, as get-todos reads it, not a pattern over the text.
    marker_differs = expect.marker is not None and (marker or NO_MARKER) != expect.marker
    if hash_differs or tree_differs or marker_differs:
        first_line = (block.get("content") or "").split("\n", 1)[0]
        expected = {}
        if expect.hash is not None:
            expected["expected_hash"] = expect.hash
        if expect.tree_hash is not None:
            expected["expected_tree_hash"] = expect.tree_hash
        if expect.marker is not None:
            expected["expected_marker"] = expect.marker
        raise PreconditionFailed(
            f"Block {str(block.get('uuid'))[:8]}.. changed since it was read "
            f'(marker is {marker or "none"}, first line: "{first_line}"). '
            "Read it again and decide again.",
            **expected, actual_marker=marker, first_line=first_line)


def read_checked(api, block_id: str, expect: Expect, *, include_children: bool = False):
    """The block a command works from: checked against ``expect`` when it has one.

    With a precondition the read goes past the cache and is checked at once,
    so a refusal comes before anything is previewed or written. Without one
    it is the plain cached read, which may be falsy for a missing block.
    """
    if not expect:
        return api.get_block(block_id, include_children=include_children)
    block = read_fresh(api, block_id, include_children=include_children)
    check_precondition(block, expect)
    return block
