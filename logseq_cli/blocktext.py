"""How Logseq reads the lines of one block's text, and what that asks of a write.

Kept apart from helpers so that LogseqAPI, which checks every write, depends
on this rule and nothing else. Measured against Logseq 0.10.15 throughout.
"""
import re
from collections import Counter
from typing import NamedTuple


def is_fence(line: str) -> bool:
    """Whether ``line`` is a code fence to Logseq.

    Measured (#43): a fence line starts, after spaces, tabs or form feeds,
    with ``` or ~~~; a no-break space or a carriage return in front, which
    str.lstrip() would also take away, makes it text.
    """
    return line.lstrip(" \t\f").startswith(("```", "~~~"))


def code_block_lines(lines: list) -> tuple:
    """Which of a block's ``lines`` are inside a code block, and the index of
    an opening fence that nothing closes (``None`` if there is none).

    Measured (#43): the next fence line closes a code block, whichever of ```
    and ~~~ it uses and whatever follows on the line. An opener that nothing
    closes makes no code block, and the lines after it are read as usual; so a
    one-line ```x``` hides nothing unless a fence line follows. Readers that
    take a line inside for text rely on this erring toward "not code": a line
    wrongly hidden, an id:: line say, would reach Logseq unchecked.
    """
    inside, opener = [False] * len(lines), None
    for i, line in enumerate(lines):
        if not is_fence(line):
            continue
        if opener is None:
            opener = i
        else:
            inside[opener + 1:i] = [True] * (i - opener - 1)
            opener = None
    return inside, opener


# Text written as ONE block has to come back from the page file as that block.
# Logseq writes it under one bullet, and its file parser reads some lines as
# block boundaries, measured (#47) by writing a block through the API,
# touching the page file and reading the page back:
#   - after the first line, "-" followed by whitespace or the line's end
#     becomes a child block, and a run of "#" followed by the same (a heading,
#     "#######" too) a block next to it; whitespace, before the mark and after
#     it, is a space, a tab, a form feed or a carriage return, so "-\r" from a
#     CRLF text counts ("-b", "#tag", "* b", "1. b" stay text);
#   - a fence nothing closes, on any line, runs on into the blocks after it up
#     to the next code block on the page and swallows them.
# Inside a closed code block none of this applies. The database keeps the
# block as sent until the file is read again, so the damage shows later.
_BULLET_LINE_RE = re.compile(r'^[ \t\f\r]*-(?:[ \t\f\r]|$)')
_HEADING_LINE_RE = re.compile(r'^[ \t\f\r]*#+(?:[ \t\f\r]|$)')

_EFFECT = {
    "bullet": "becomes a block of its own under this one",
    "heading": "becomes a block of its own next to this one",
    "open_fence": ("opens a code block nothing in the block closes, and this "
                   "block takes in the blocks after it, up to the next code "
                   "block on the page"),
}


class Boundary(NamedTuple):
    """A line of a block's text that would not stay in that block."""
    line: int
    kind: str       # bullet, heading or open_fence
    text: str


def block_boundaries(content: str) -> list:
    """Where ``content``, written as one block, would not come back as one:
    a :class:`Boundary` for each such line. Empty when it comes back whole."""
    lines = (content or "").split("\n")
    code, unclosed = code_block_lines(lines)
    found = []
    for index, line in enumerate(lines):
        if index == unclosed:
            found.append(Boundary(index + 1, "open_fence", line))
        elif index and not code[index]:
            if _BULLET_LINE_RE.match(line):
                found.append(Boundary(index + 1, "bullet", line))
            elif _HEADING_LINE_RE.match(line):
                found.append(Boundary(index + 1, "heading", line))
    return found


class SplitBlockError(Exception):
    """Text that would not come back from the page file as the one block it is
    written as. Carries a ready-to-print message, and ``line`` and ``kind`` for
    a JSON error. Not a ValueError: the commands catch those around their input
    checks, and this has to reach handle_connection_error for its exit code and
    JSON form."""

    def __init__(self, message: str, line: int = None, kind: str = None):
        super().__init__(message)
        self.line = line
        self.kind = kind


# The way out, per input: what the text probably meant, and how to write it
# there. Outline text never gets here with a bullet or a heading line, which it
# takes as blocks of their own; only an unclosed fence reaches the check.
_WAYS = {
    "insert-block": ("  - want children?           -> indent them with a tab\n"
                     "  - want blocks side by side? -> insert-block --tree (or --tree-file FILE)"),
    "insert-block --tree": ("  - want children?           -> put them into the node's \"children\"\n"
                            "  - want blocks side by side? -> make them nodes of their own"),
    "add-journal-block": ("  - want children?           -> indent them with a tab\n"
                          "  - want blocks side by side? -> pass --content several times\n"
                          "  - from a file?              -> --content-file FILE"),
    "update-block": ("  - only change the line?     -> shorten --content to a single line\n"
                     "  - want children?           -> insert-block --child-of UUID\n"
                     "  - want a block after it?    -> insert-block --after UUID"),
    "create-page": "  - want structure?           -> create the page, then add-note-content",
    "add-journal-entry": "  - want structure?           -> add-journal-block with indented --content",
    "copy-block": ("  - relocating it?            -> move-block keeps the block as it is\n"
                   "  - copying it?               -> split the source block first"),
    "replace-text": "  - choose a --replace that starts no line with - or #",
    "set-todo-status": ("  - the marker goes in front of the first line, and this block starts\n"
                        "    with a code fence: give it a first line of text (update-block)"),
}
_CLOSE_FENCE = ("  - close it                  -> end the code with a line that starts "
                "with ``` (or ~~~); in outline text a line without a bullet")
_FENCE_WAYS = {
    "add-journal-entry": _CLOSE_FENCE + "\n  - keep the code in one block -> leave out --multi-block",
    # The marker in front of an opening fence makes it text, and the closing
    # fence is left open: nothing the caller wrote.
    "set-todo-status": _WAYS["set-todo-status"],
}


def _refusal(boundary: Boundary, command: str, where: str, ways: str = None) -> SplitBlockError:
    number, kind, line = boundary
    if ways is None:
        ways = (_FENCE_WAYS.get(command, _CLOSE_FENCE) if kind == "open_fence"
                else _WAYS.get(command, ""))
    return SplitBlockError(
        f"{where}, line {number} ({line!r}): when Logseq reads the page file "
        f"again, this line {_EFFECT[kind]}. {command.split()[0]} writes each "
        "block's text as given, so the block would not stay the one written. "
        "In a closed code block such lines are fine. Nothing was written."
        + ("\n" + ways if ways else ""),
        line=number, kind=kind)


def refuse_split_block(content: str, *, command: str, where: str = "--content",
                       replacing: str = None, ways: str = None) -> None:
    """Refuse ``content`` if it would not come back as the one block written.

    Every write in LogseqAPI goes through this, whichever command sent it; the
    commands call it first, for their own way out in the message (``command``
    picks it, ``ways`` replaces it). With ``replacing`` (the text an update
    replaces), a line the block already had passes: Logseq's own editor makes
    such blocks, and changing another line of one is no reason to refuse.

    Raises:
        SplitBlockError: naming the line, what Logseq would make of it, and
            the way to write it.
    """
    # Counted per kind: an update may change the text of such a line the block
    # had, but not add one, whether by repeating it or by freeing one from a
    # code block.
    had = Counter(b.kind for b in block_boundaries(replacing or ""))
    for boundary in block_boundaries(content):
        if had[boundary.kind]:
            had[boundary.kind] -= 1
        else:
            raise _refusal(boundary, command, where, ways)


def refuse_split_tree(tree: list, *, command: str, label: str = "The block",
                      single_label: str = "--content") -> None:
    """:func:`refuse_split_block` for every block of ``tree``, before any of
    it is written, so a refusal never leaves part of it behind. A tree of one
    block is named ``single_label`` (the text as given); in a larger one each
    block by ``label`` and its first line."""
    alone = len(tree or []) == 1 and not tree[0].get("children")

    def walk(blocks):
        for node in blocks or []:
            if not isinstance(node, dict):
                continue
            content = node.get("content", "") or ""
            first = content.split("\n")[0]
            refuse_split_block(content, command=command,
                               where=single_label if alone else f"{label} {first[:40]!r}")
            walk(node.get("children"))
    walk(tree)


def refuse_split_heading(heading: str, *, command: str) -> None:
    """:func:`refuse_split_block` for the heading ``command`` writes a block
    under (--under-heading, or its default from the config or
    LOGSEQ_JOURNAL_HEADING). It is a block of its own, and may be written
    first, so it is checked before the command writes anything."""
    if heading:
        refuse_split_block(heading, command=command, where="The heading",
                           ways="  - name one heading          -> a single line, "
                                "such as \"## Log\"")


def refuse_split_property(key: str, value) -> None:
    """Refuse a property value with a line break in it.

    Logseq writes the value into the block's text as ``key:: value``, so each
    line after a break is a line of the block: "v\n- x" put "x" into a child
    block when the file was read again (measured, upsertBlockProperty,
    0.10.15), and a line "custom-id:: y" or "k:: v" would be read as a
    property. A value is one line in Logseq's file format, so any break is
    refused, not only one that starts a block.
    """
    if isinstance(value, str) and "\n" in value:
        second = value.split("\n")[1]
        raise SplitBlockError(
            f"The value of {key}, line 2 ({second!r}): a property value is one "
            f"line. Logseq writes it into the block as '{key}:: value', and every "
            "line after a break would be read as a line of the block of its own: "
            "a block, a property, or the block's id. Nothing was written.\n"
            "  - one line only            -> join the lines, or separate values with commas",
            line=2, kind="line_break")
