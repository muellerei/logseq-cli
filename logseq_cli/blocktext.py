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


# A block's content carries its property lines verbatim (id:: <uuid>, key:: val).
# This is the one rule for what counts as one; every reader that tells
# property lines from text goes through it or through property_line_mask,
# which also knows code fences. (No list of readers here: it would drift.)
#
# It is the rule Logseq reads by, measured against 0.10.15 by writing lines
# into a page file and reading :block/properties back. A key ends at
# whitespace or at one of PROPERTY_KEY_STOP, may not start with '#', and '::'
# is followed by a space or the end of the line (a tab does not count). So
# 'logseq.order-list-type:: number', which Logseq writes for numbered lists,
# 'k::' and an indented '  k:: v' are properties; 'std::cout', 'k::v' and
# 'a,b:: x' are text. The stop characters are the ones #21 measured for the
# writer, so what set-property writes and what this reads cannot disagree;
# '/' alone differs, see helpers._PROPERTY_KEY_FORBIDDEN. What may indent the line is
# measured too (#43): spaces, tabs, form feeds and carriage returns, not a
# no-break space or a vertical tab. Missing one here is the unsafe direction:
# an id:: line the CLI took for text would still be the block's id.
PROPERTY_KEY_STOP = r':,;\\\[\](){}|^"@~`'
_INDENT = r'[ \t\f\r]*'
PROPERTY_LINE_RE = re.compile(rf'^{_INDENT}(?!#)[^\s{PROPERTY_KEY_STOP}]+::(?: |$)')


def property_line_mask(lines: list) -> list:
    """For each line of a block's content, whether Logseq reads it as a property.

    PROPERTY_LINE_RE judges one line alone; inside a code block the same text
    is code, not a property. Readers that walk a block's lines take this mask,
    so a --find inside a fenced example is replaced and a get-todos --match
    sees it.

    The code block is Logseq's, see code_block_lines (#43).
    """
    inside, _ = code_block_lines(lines)
    # A fence line itself is never a property: ` and ~ end a key.
    return [not code and bool(PROPERTY_LINE_RE.match(line))
            for line, code in zip(lines, inside)]


# An ``id::`` line inside a block's content names the UUID that block is meant
# to keep. Logseq only honours it when the write asks for it (``keepUUID`` on
# insertBatchBlock, which every --keep-ids write goes through since #31);
# otherwise it mints a fresh one and drops the id, which leaves every ((uuid))
# pointing at the old one dangling. Verified against a live graph, both ways.
#
# Logseq reads more than one spelling as the block's id: keys are lower-cased,
# and custom-id / custom_id are renamed to id (extract-properties, measured in
# #21). A copied block also carries the line indented, as it sits in the file.
# Every spelling counts, or one of them would slip past the checks and still
# set the uuid. The separator is PROPERTY_LINE_RE's: "id::x" without the
# space is text to Logseq (measured as "k::v", #39) and must not be dropped.
# Logseq trims the value: a tab after the space, and a tab, form feed,
# vertical tab, no-break space or ideographic space after the value, still
# leave the line the block's id. Only a trailing "\r" does not (all measured,
# 0.10.15, #56). What is left is the value, spaces inside included: Logseq
# takes "id:: a b c" as the block's uuid too (measured, #56). A rule that
# stopped at the first space, or at a no-break space, let such a line past
# every check here.
#
# An id:: line inside a code block is code to Logseq, not the block's id
# (measured, #43), so only the lines property_line_mask passes count. That
# holds only as long as the check, the removal and the write see the same
# blocks: the commands check and clean the parsed outline they write, never
# the raw text, where a fence opened on a bullet line reads differently.
_TRIM = r'[^\S\r\n]*'  # whitespace Logseq trims off the value, but not "\r"
_ID_PROPERTY_RE = re.compile(rf'^{_INDENT}(?:id|custom[-_]id):: +{_TRIM}(\S(?:[^\r\n]*\S)?){_TRIM}$',
                             re.MULTILINE | re.IGNORECASE)


def id_lines(content: str) -> list:
    """``(line, id value or None)`` for each line of one block's ``content``."""
    lines = (content or "").split("\n")
    found = []
    for line, is_property in zip(lines, property_line_mask(lines)):
        match = _ID_PROPERTY_RE.fullmatch(line) if is_property else None
        found.append((line, match.group(1) if match else None))
    return found


def block_id_property(content: str) -> str:
    """The ``id::`` value in ``content``, or ``""`` if it carries none."""
    return next((value for _, value in id_lines(content) if value), "")


def without_block_ids(content: str) -> str:
    """``content`` with its ``id::`` lines removed, in every spelling.

    What "dropped" has to mean when an id is not kept: left in the content,
    the line would name a uuid the block does not have, and a copy would put
    the original's id into the file, where the next parse finds two blocks
    claiming it. A line in a code block is code and stays.
    """
    return "\n".join(line for line, value in id_lines(content) if not value)


def refuse_id_lines(content: str, *, own: str = None, replacing: str = None,
                    where: str = "The text") -> None:
    """Refuse ``content`` if it carries an ``id::`` line, other than one naming
    ``own``, the uuid of the block an update writes, or one ``replacing`` (the
    text the update replaces) already had.

    Every write in LogseqAPI goes through this, as through refuse_split_block:
    the commands decide first what their contract with such a line is (drop it
    and say so, keep it with --keep-ids, refuse), and this is where a line no
    command decided on stops. Written, Logseq takes the line as the block's
    uuid once it reads the page file again (measured, #56), and every ((ref))
    to the block points at nothing. The block's own line is what getBlock
    hands out, and writing it back keeps the uuid (measured). A line the block
    had passes as well, as in refuse_split_block: a copy carries its source's
    line until the file is read again (measured), and changing another line
    of it changes nothing about that.

    Raises:
        IdLineError: naming the line.
    """
    allowed = {(own or "").lower()} | {value.lower() for _, value in id_lines(replacing or "")
                                        if value}
    for number, (line, value) in enumerate(id_lines(content), 1):
        if value and value.lower() not in allowed:
            raise IdLineError(
                f"{where}, line {number} ({line!r}): Logseq reads this line as "
                "the block's id, and would give the block this uuid when it "
                "reads the page file again; every ((ref)) to the block would "
                "then point at nothing. Nothing was written.", line=number)


def refuse_id_lines_tree(tree: list) -> None:
    """:func:`refuse_id_lines` for every block of ``tree``, before any of it is
    written."""
    for content, where in _labelled_blocks(tree, "The block"):
        refuse_id_lines(content, where=where)


class IdLineError(Exception):
    """An ``id::`` line that would reach Logseq without a command having decided
    on it. Carries ``line`` for a JSON error, like :class:`SplitBlockError`,
    and is answered like it: refused before the write."""

    def __init__(self, message: str, line: int = None):
        super().__init__(message)
        self.line = line


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
    for content, where in _labelled_blocks(tree, label):
        refuse_split_block(content, command=command,
                           where=single_label if alone else where)


def _labelled_blocks(tree: list, label: str):
    """``(content, name)`` for every block of ``tree``, DFS pre-order, the
    name being ``label`` and the block's first line: how a refusal of a tree
    names the block it stopped at."""
    for node in tree or []:
        if isinstance(node, dict):
            content = node.get("content", "") or ""
            yield content, f"{label} {content.split(chr(10))[0][:40]!r}"
            yield from _labelled_blocks(node.get("children"), label)


def refuse_split_heading(heading: str, *, command: str) -> None:
    """:func:`refuse_split_block` for the heading ``command`` writes a block
    under (--under-heading, or its default from the config or
    LOGSEQ_JOURNAL_HEADING). It is a block of its own, and may be written
    first, so it is checked before the command writes anything."""
    if heading:
        refuse_split_block(heading, command=command, where="The heading",
                           ways="  - name one heading          -> a single line, "
                                "such as \"## Log\"")
        # Checked here too, not only in LogseqAPI: by the time the heading is
        # written, the page it goes on may have been created (#56).
        refuse_id_lines(heading, where="The heading")


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
