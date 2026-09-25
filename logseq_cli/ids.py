"""What the CLI does with an Id Line in text it writes.

A write without ``--keep-ids`` drops the line, says so in a Note, and refuses
text that was nothing but such lines; update-block keeps a block's own line
and drops one naming another uuid. With ``--keep-ids`` every id has to be a
well-formed uuid, used once and one per block, and held by no block or page,
or the write is refused before anything is sent. blocktext holds how Logseq
reads such a line; this module holds the CLI's contract about it, which is
why it words the Notes and asks the graph, and blocktext does neither.
"""

import re

import click

from logseq_cli.blocktext import block_id_property, id_lines, tree_texts, without_block_ids


# Logseq stores block ids as RFC 4122 UUIDs. A value that is not one cannot
# become a block id, so a tree carrying one has to be refused before the write
# rather than after: the batch call answers null either way and the per-block
# call would simply ignore the option.
_UUID_RE = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$',
    re.IGNORECASE,
)


def tree_without_block_ids(tree: list) -> list:
    """``tree`` with :func:`without_block_ids` applied to every node."""
    return [
        {**block,
         "content": without_block_ids(block.get("content", "")),
         "children": tree_without_block_ids(block.get("children") or [])}
        for block in tree or [] if isinstance(block, dict)
    ]


def collect_block_ids(tree: list) -> list:
    """Every ``id::`` value in ``tree``, DFS pre-order.

    Used both to warn that ids would be dropped and to validate them before a
    write that promises to keep them.
    """
    found = []
    for block in tree or []:
        if not isinstance(block, dict):
            continue
        value = block_id_property(block.get("content", ""))
        if value:
            found.append(value)
        found.extend(collect_block_ids(block.get("children") or []))
    return found


def blocks_with_several_ids(tree: list) -> list:
    """The ``id::`` values of every block in ``tree`` that carries more than one."""
    found = []
    for block in tree or []:
        if not isinstance(block, dict):
            continue
        values = [value for _, value in id_lines(block.get("content", "")) if value]
        if len(values) > 1:
            found.extend(values)
        found.extend(blocks_with_several_ids(block.get("children") or []))
    return found


def invalid_block_ids(tree: list) -> list:
    """The ``id::`` values in ``tree`` that are not RFC 4122 UUIDs."""
    return [v for v in collect_block_ids(tree) if not _UUID_RE.match(v)]


class BlockIdError(ValueError):
    """The id:: contract refuses the content: ``--keep-ids`` cannot be
    honoured, or without it nothing but ids was sent. ``field`` names the
    offending ``ids`` in a JSON error (``ambiguous_ids``, ``repeated_ids``,
    ``invalid_ids``, ``existing_ids`` or ``dropped_ids``)."""

    def __init__(self, message: str, field: str, ids: list):
        super().__init__(message)
        self.field = field
        self.ids = ids


# The one wording for text that dropping its id:: lines left empty (#56, #67).
BESIDES_IDS = "holds nothing but id:: lines, and those are dropped"
NOTHING_BESIDES_IDS = f"The content {BESIDES_IDS}: nothing is left to write. Nothing was written."


def uuids_in_use(api, ids: list) -> list:
    """Those of ``ids`` (well-formed uuids) that a block or a page has.

    Named positively, by what the entity carries (measured, 0.10.15): a block
    has ``:block/page``, a page ``:block/name``. The placeholder Logseq keeps
    for a ``((ref))`` whose target does not exist has neither, and a
    --keep-ids write takes it over (#31). Asking only for ``:block/page`` let
    a page's uuid through as if it were a placeholder.
    """
    if not ids:
        return []
    literals = " ".join(f'#uuid "{i.lower()}"' for i in ids)
    rows = api.datascript_query(
        f"[:find ?u :where [?b :block/uuid ?u] "
        f"(or [?b :block/page _] [?b :block/name _]) "
        f"[(contains? #{{{literals}}} ?u)]]") or []
    found = {str(row[0]).lower() for row in rows if row}
    return [i for i in ids if i.lower() in found]


def dropped_ids_note(count: int, way: str = None) -> str:
    """The note for ``count`` id:: lines a write drops, with ``way`` to keep
    them; the default is the --keep-ids of the commands that have it."""
    return (f"Note: {count} id:: propert(ies) in the content will be dropped; "
            "Logseq mints new UUIDs and any ((uuid)) pointing at the old ones "
            "will dangle. " + (way or "Pass --keep-ids to preserve them (for "
                               "moving or restoring an outline; ids that still "
                               "exist are refused)."))


def without_block_ids_noted(contents: list) -> tuple:
    """``contents``, the texts of blocks a writer without --keep-ids sends,
    with their id:: lines removed, and the note saying so (``None`` if there
    were none). An id:: line would become the block's uuid (#56)."""
    dropped = collect_block_ids([{"content": c} for c in contents])
    if not dropped:
        return contents, None
    return [without_block_ids(c) for c in contents], dropped_ids_note(
        len(dropped), way="To write a block under a uuid of your choosing, use "
                          "insert-block --keep-ids.")


def without_foreign_block_ids(content: str, own: str) -> tuple:
    """``content`` without the id:: lines that name another uuid than
    ``own``, and the note saying so (``None`` if there were none).

    For an update of the block ``own``: its own line is what getBlock hands
    out, and writing it back keeps the uuid (measured, #56). Another one would
    become the block's uuid once Logseq reads the file again, and leave every
    ((ref)) to it dangling, so it goes, as it would in any other write.
    """
    kept, dropped = [], 0
    for line, value in id_lines(content):
        if value and value.lower() != own.lower():
            dropped += 1
        else:
            kept.append(line)
    if not dropped:
        return content, None
    return "\n".join(kept), (
        f"Note: {dropped} id:: line(s) naming another block's uuid will be "
        "dropped: a block keeps its uuid when its text changes, and taking "
        "that one would leave every ((ref)) to this block dangling. move-block "
        "moves a block with its uuid.")


def check_block_ids(api, tree: list, keep_ids: bool):
    """Apply the ``id::`` contract to ``tree`` before any of it is written.

    insert-block (--tree and --content), add-note-content, add-journal-block
    and add-journal-content go through this, so none of them can drop an id
    in silence again (#1 fixed insert-block --tree alone, and the others kept
    the defect). The writers without --keep-ids decide their own contract, and
    LogseqAPI refuses a line none of them decided on (#56). Returns a note for
    stderr when ids would be dropped, or ``None``. Without ``keep_ids`` raises
    :class:`BlockIdError` when dropping them leaves no text at all. With
    ``keep_ids`` raises it for
    an id that cannot become a block id, and for one a block already has:
    that is the copy case, and insertBatchBlock would give the uuid to a second
    block without a word (measured), leaving two blocks one uuid.
    """
    ids = collect_block_ids(tree)
    if not ids:
        return None
    if not keep_ids:
        # Text that was nothing but its ids leaves nothing to write, and an
        # empty block, or with --upsert-heading an emptied one, is no success
        # (#67). One empty block among others stays: a copied block that held
        # only its id was empty.
        if not any(without_block_ids(t).strip() for t in tree_texts(tree)):
            raise BlockIdError(NOTHING_BESIDES_IDS, "dropped_ids", ids)
        return dropped_ids_note(len(ids))
    several = blocks_with_several_ids(tree)
    if several:
        # Only one can be the block's id, and which one a batch keeps is not
        # for the CLI to guess: the other would reach the graph unchecked.
        raise BlockIdError(
            f"A block carries more than one id:: line: {', '.join(several[:3])}"
            f"{' ...' if len(several) > 3 else ''}. A block has one uuid; keep the "
            "line it should have. Nothing was written.", "ambiguous_ids", several)
    folded = [i.lower() for i in ids]  # Logseq's uuids are lower-case
    repeated = sorted({i for i in ids if folded.count(i.lower()) > 1})
    if repeated:
        # insertBatchBlock does not check (measured): both blocks would get
        # the uuid, and the graph would hold two blocks under one id.
        raise BlockIdError(
            f"{len(repeated)} id:: value(s) appear more than once in the content: "
            f"{', '.join(repeated[:3])}{' ...' if len(repeated) > 3 else ''}. "
            "One uuid can belong to one block only. Nothing was written.",
            "repeated_ids", repeated)
    bad = invalid_block_ids(tree)
    if bad:
        raise BlockIdError(
            f"{len(bad)} id:: value(s) are not valid UUIDs and cannot become "
            f"block ids: {', '.join(bad[:3])}{' ...' if len(bad) > 3 else ''}. "
            "Nothing was written.", "invalid_ids", bad)
    taken = uuids_in_use(api, ids)
    if taken:
        raise BlockIdError(
            f"{len(taken)} id:: value(s) already belong to a block or page in the graph: "
            f"{', '.join(taken[:3])}{' ...' if len(taken) > 3 else ''}. Keeping "
            "them would give two blocks one uuid; drop --keep-ids to copy with "
            "new ids, or move the original with move-block. Nothing was written.",
            "existing_ids", taken)
    # An id that only a ((ref)) still holds is not refused: Logseq keeps a
    # placeholder under it, which insert_tree_keeping_ids takes over (#31).
    return None


def require_text_besides_ids(content: str) -> str:
    """Refuse text that dropping its id:: lines left empty (#56): there is
    nothing the caller meant left to write, and writing an empty block, or
    emptying the one updated, reports a success that is none."""
    if not content.strip():
        raise click.BadParameter(f"--content {BESIDES_IDS}: nothing is left to write")
    return content
